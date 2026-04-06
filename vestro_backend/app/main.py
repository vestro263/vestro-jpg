import asyncio
import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

from .database import init_db, get_db
from .config import get_settings
from .routes.api import router as api_router, _refresh_firms
from .routes.stream import router as stream_router
from .routes.auth import router as auth_router
from .routes.trade import router as trade_router
from .workers.scheduler import create_scheduler
from app.services.signal_engine import run_signal_loop
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db import engine
from app.models import Base


# ------------------ LOGGING ------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger(__name__)
settings = get_settings()

# ------------------ LIFESPAN ------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Vestro backend starting up")

    await init_db()
    log.info("DB tables ready")

    await _refresh_firms()
    log.info("Price cache ready")

    # Restore bot state
    import vestro_backend.app.routes.api as api_module
    api_module._bot_running = api_module._read_bot_state()
    log.info(f"Bot state restored: running={api_module._bot_running}")

    asyncio.create_task(run_signal_loop())
    log.info("Signal engine running")

    scheduler = create_scheduler()
    scheduler.start()
    log.info("Scheduler running")

    yield

    scheduler.shutdown(wait=False)
    log.info("Vestro backend shut down")


# ------------------ APP INIT ------------------
app = FastAPI(
    title="Vestro Valuation Engine",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)

# ------------------ CORS ------------------
ALLOWED_ORIGINS = [
    "https://r3bel-production.up.railway.app",
    "https://vestro-ui.onrender.com",
    "http://localhost:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




# ------------------ GLOBAL ERROR HANDLER ------------------
@app.exception_handler(Exception)
async def debug_exception_handler(request: Request, exc: Exception):
    log.error("Unhandled error: %s", str(exc))
    log.error(traceback.format_exc())

    response = JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "traceback": traceback.format_exc(),
        },
    )

    # ✅ Ensure CORS headers are ALWAYS present
    origin = request.headers.get("origin")
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
    else:
        response.headers["Access-Control-Allow-Origin"] = "*"

    response.headers["Access-Control-Allow-Credentials"] = "true"

    return response

@app.get("/debug/ml")
async def check_ml_data(db: AsyncSession = Depends(get_db)):

    results = {}

    ml_tables = ["signal_logs", "calibration_config"]

    for table in ml_tables:
        try:
            count = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
            results[table] = {"count": count.scalar()}
        except Exception as e:
            results[table] = {"error": str(e)}

    # Check labeled vs unlabeled signal_logs
    try:
        labeled = await db.execute(text(
            "SELECT COUNT(*) FROM signal_logs WHERE label_15m IS NOT NULL"
        ))
        unlabeled = await db.execute(text(
            "SELECT COUNT(*) FROM signal_logs WHERE label_15m IS NULL"
        ))
        recent = await db.execute(text(
            "SELECT strategy, signal, confidence, captured_at FROM signal_logs ORDER BY captured_at DESC LIMIT 5"
        ))
        rows = recent.fetchall()
        results["signal_logs"]["labeled"]   = labeled.scalar()
        results["signal_logs"]["unlabeled"] = unlabeled.scalar()
        results["signal_logs"]["recent"]    = [
            {"strategy": r[0], "signal": r[1], "confidence": r[2], "captured_at": str(r[3])}
            for r in rows
        ]
    except Exception as e:
        results["signal_logs"]["detail_error"] = str(e)

    # Check calibration config
    try:
        configs = await db.execute(text(
            "SELECT symbol, strategy, precision, f1, n_samples, trained_at FROM calibration_config"
        ))
        results["calibration_config"]["rows"] = [
            {"symbol": r[0], "strategy": r[1], "precision": r[2],
             "f1": r[3], "n_samples": r[4], "trained_at": str(r[5])}
            for r in configs.fetchall()
        ]
    except Exception as e:
        results["calibration_config"]["detail_error"] = str(e)

    return results

@app.get("/debug/tables")
async def list_tables(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))
    return {"tables": [r[0] for r in result.fetchall()]}

@app.get("/debug/creds")
async def check_creds(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text("SELECT id, user_id, broker FROM credentials"))
    rows = result.fetchall()
    return {"count": len(rows), "rows": [{"id": r[0], "user_id": r[1], "broker": r[2]} for r in rows]}

@app.get("/debug/ml-detail")
async def ml_detail(db: AsyncSession = Depends(get_db)):

    r1 = await db.execute(text("SELECT signal, COUNT(*) FROM signal_logs GROUP BY signal"))
    r2 = await db.execute(text("SELECT COUNT(*) FROM signal_logs WHERE entry_price IS NOT NULL"))
    r3 = await db.execute(text("SELECT COUNT(*) FROM signal_logs WHERE entry_price IS NULL"))
    return {
        "signals_breakdown": {r[0]: r[1] for r in r1.fetchall()},
        "has_entry_price":   r2.scalar(),
        "no_entry_price":    r3.scalar(),
    }

@app.get("/debug/run-trainer")
async def debug_run_trainer():
    import traceback
    try:
        from ml.calibration_trainer import run_trainer
        await run_trainer()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e), "trace": traceback.format_exc()}

@app.get("/debug/reload-calibration")
async def debug_reload_calibration():
    import traceback
    try:
        from ml.calibration_loader import force_reload
        await force_reload()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e), "trace": traceback.format_exc()}

@app.get("/debug/symbols")
async def debug_symbols(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text(
        "SELECT symbol, strategy, COUNT(*) as cnt FROM signal_logs GROUP BY symbol, strategy"
    ))
    return [{"symbol": r[0], "strategy": r[1], "count": r[2]} for r in result.fetchall()]

@app.get("/debug/crash500-labels")
async def debug_crash500_labels(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text("""
        SELECT signal, label_15m, COUNT(*) as cnt 
        FROM signal_logs 
        WHERE symbol = 'CRASH500' 
        GROUP BY signal, label_15m
        ORDER BY signal, label_15m
    """))
    return [{"signal": r[0], "label": r[1], "count": r[2]} for r in result.fetchall()]

@app.get("/debug/crash500-spikes")
async def debug_crash500_spikes(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text("""
        SELECT 
            ROUND(AVG(drop_spike::numeric), 4) as avg_spike,
            ROUND(MAX(drop_spike::numeric), 4) as max_spike,
            ROUND(MIN(drop_spike::numeric), 4) as min_spike,
            PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY drop_spike) as p90_spike,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY drop_spike) as p95_spike,
            COUNT(*) as total
        FROM signal_logs
        WHERE symbol = 'CRASH500' AND drop_spike IS NOT NULL
    """))
    row = result.fetchone()
    return {
        "avg": row[0], "max": row[1], "min": row[2],
        "p90": row[3], "p95": row[4], "total": row[5]
    }

@app.get("/debug/label-dist")
async def label_dist(db: AsyncSession = Depends(get_db)):

    r = await db.execute(text("""
        SELECT label_15m, COUNT(*) 
        FROM signal_logs 
        WHERE label_15m IS NOT NULL
        GROUP BY label_15m
        ORDER BY label_15m
    """))
    mapping = {-1: "LOSS", 0: "NEUTRAL", 1: "WIN"}
    return {mapping.get(row[0], str(row[0])): row[1] for row in r.fetchall()}
# ------------------ ROUTES ------------------
app.include_router(api_router)
app.include_router(stream_router)
app.include_router(auth_router)
app.include_router(trade_router)