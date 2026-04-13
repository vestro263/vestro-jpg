import asyncio
import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends
from pydantic import BaseModel
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
    api_module._bot_running = True
    api_module._write_bot_state(True)
    log.info("Bot auto-started on deploy")




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

@app.post("/api/calibration/train")
async def trigger_training():
    import asyncio
    from ml.calibration_trainer import run_trainer
    asyncio.create_task(run_trainer())
    return {"status": "training started"}

@app.get("/debug/run-labeler-verbose")
async def run_labeler_verbose():
    from app.database import AsyncSessionLocal
    from app.models import Credentials
    from app.services.credential_store import decrypt
    from ml.outcome_labeler import run_labeler
    from sqlalchemy import select
    import traceback

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Credentials))
            creds  = result.scalars().all()

        cred = next(
            (c for c in creds if c.broker == "deriv" and c.user_id.startswith("VRTC")),
            None
        )

        if not cred:
            return {"status": "error", "detail": "no VRTC credential found"}

        await run_labeler(decrypt(cred.password))   # await directly, no create_task
        return {"status": "done"}

    except Exception as e:
        return {
            "status":  "crashed",
            "error":   str(e),
            "trace":   traceback.format_exc(),
        }


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


@app.get("/debug/execution-window")
async def execution_window(
    db: AsyncSession = Depends(get_db),
    min_confidence: float = 0.60,
    strategy: str | None = None,
    signal: str | None = None,
    executed_only: bool = False,      # ← add this
    limit: int = 200,
):
    filters = [
        "confidence >= :conf",
        "signal IN ('BUY', 'SELL')",
    ]
    params: dict = {"conf": min_confidence, "limit": limit}

    if strategy:
        filters.append("strategy = :strategy")
        params["strategy"] = strategy
    if signal:
        filters.append("signal = :signal")
        params["signal"] = signal
    if executed_only:
        filters.append("executed = true")   # ← only real fired trades

    where = " AND ".join(filters)

    rows = await db.execute(text(f"""
        SELECT id, strategy, symbol, signal, confidence,
               entry_price, exit_price, outcome, executed,
               executed_at, captured_at
        FROM signal_logs
        WHERE {where}
        ORDER BY captured_at DESC
        LIMIT :limit
    """), params)

    data = [
        {
            "id":           r[0],
            "strategy":     r[1],
            "symbol":       r[2],
            "signal":       r[3],
            "confidence":   r[4],
            "entry_price":  r[5],
            "exit_price":   r[6],
            "outcome":      r[7],
            "executed":     r[8],
            "executed_at":  str(r[9]) if r[9] else None,
            "captured_at":  str(r[10]),
        }
        for r in rows.fetchall()
    ]

    closed = [d for d in data if d["outcome"] in ("WIN", "LOSS")]
    wins   = sum(1 for d in closed if d["outcome"] == "WIN")
    losses = len(closed) - wins

    return {
        "threshold":     min_confidence,
        "total":         len(data),
        "wins":          wins,
        "losses":        losses,
        "open":          sum(1 for d in data if not d["outcome"]),
        "win_rate":      round(wins / len(closed), 4) if closed else None,
        "executed_only": executed_only,
        "signals":       data,
    }


@app.post("/api/signal/mark-executed")
async def mark_signal_executed(
        payload: dict,
        db: AsyncSession = Depends(get_db),
):
    signal_id = payload.get("signal_id")
    if not signal_id:
        raise HTTPException(status_code=400, detail="signal_id required")

    await db.execute(text("""
        UPDATE signal_logs
        SET executed = true, executed_at = NOW()
        WHERE id = :id
    """), {"id": signal_id})
    await db.commit()
    return {"status": "ok", "id": signal_id}



class OutcomeUpdate(BaseModel):
    signal_id:  str
    exit_price: float
    outcome:    str   # "WIN" | "LOSS" | "NEUTRAL"

@app.post("/signal/outcome")
async def record_outcome(payload: OutcomeUpdate, db: AsyncSession = Depends(get_db)):
    if payload.outcome not in ("WIN", "LOSS", "NEUTRAL"):
        raise HTTPException(status_code=400, detail="outcome must be WIN, LOSS, or NEUTRAL")

    result = await db.execute(text("""
        UPDATE signal_logs
        SET outcome    = :outcome,
            exit_price = :exit_price,
            label_15m  = :label_int
        WHERE id = :id
        RETURNING id, strategy, signal, confidence, entry_price, outcome
    """), {
        "outcome":    payload.outcome,
        "exit_price": payload.exit_price,
        "label_int":  1 if payload.outcome == "WIN" else (-1 if payload.outcome == "LOSS" else 0),
        "id":         payload.signal_id,
    })

    row = result.fetchone()
    await db.commit()

    if not row:
        raise HTTPException(status_code=404, detail=f"Signal {payload.signal_id} not found")

    return {
        "status":     "updated",
        "id":         row[0],
        "strategy":   row[1],
        "signal":     row[2],
        "confidence": row[3],
        "entry":      row[4],
        "outcome":    row[5],
    }


@app.get("/debug/walk-forward")
async def debug_walk_forward(db: AsyncSession = Depends(get_db)):
    result = {}

    # ── 1. How many labeled rows exist per symbol ─────────────────────────────
    try:
        r = await db.execute(text("""
            SELECT symbol, strategy,
                   COUNT(*)                                          AS total,
                   COUNT(*) FILTER (WHERE label_15m IS NOT NULL)    AS labeled,
                   COUNT(*) FILTER (WHERE label_15m IS NULL)        AS unlabeled,
                   COUNT(*) FILTER (WHERE outcome IS NOT NULL)      AS has_outcome,
                   MIN(captured_at)                                  AS oldest,
                   MAX(captured_at)                                  AS newest
            FROM signal_logs
            WHERE signal != 'HOLD'
            GROUP BY symbol, strategy
            ORDER BY symbol
        """))
        result["labeled_rows"] = [
            {
                "symbol": row[0],
                "strategy": row[1],
                "total": row[2],
                "labeled": row[3],
                "unlabeled": row[4],
                "has_outcome": row[5],
                "oldest": str(row[6]),
                "newest": str(row[7]),
            }
            for row in r.fetchall()
        ]
    except Exception as e:
        result["labeled_rows"] = {"error": str(e)}

    # ── 2. Calibration models in DB ───────────────────────────────────────────
    try:
        r = await db.execute(text("""
            SELECT symbol, strategy, precision, recall, f1, n_samples,
                   confidence_min, trained_at
            FROM calibration_config
            ORDER BY trained_at DESC
        """))
        rows = r.fetchall()
        result["calibration_models"] = [
            {
                "symbol": row[0],
                "strategy": row[1],
                "precision": row[2],
                "recall": row[3],
                "f1": row[4],
                "n_samples": row[5],
                "confidence_min": row[6],
                "trained_at": str(row[7]),
            }
            for row in rows
        ]
        result["models_exist"] = len(rows) > 0
    except Exception as e:
        result["calibration_models"] = {"error": str(e)}

    # ── 3. Walk-forward readiness check ──────────────────────────────────────
    MIN_SAMPLES = 200  # must match walk_forward_validator.py MIN_SAMPLES

    readiness = {}
    for entry in result.get("labeled_rows", []):
        if isinstance(entry, dict) and "symbol" in entry:
            sym = entry["symbol"]
            labeled = entry["labeled"]
            readiness[sym] = {
                "labeled": labeled,
                "min_required": MIN_SAMPLES,
                "ready": labeled >= MIN_SAMPLES,
                "need_more": max(0, MIN_SAMPLES - labeled),
            }
    result["walk_forward_readiness"] = readiness

    # ── 4. Signal outcome fill rate (journal health) ──────────────────────────
    try:
        r = await db.execute(text("""
            SELECT
                COUNT(*)                                        AS total_signals,
                COUNT(*) FILTER (WHERE outcome IS NOT NULL)    AS with_outcome,
                COUNT(*) FILTER (WHERE exit_price IS NOT NULL) AS with_exit_price,
                COUNT(*) FILTER (WHERE executed = true)        AS executed,
                COUNT(*) FILTER (WHERE executed = true
                    AND outcome IS NOT NULL)                    AS executed_and_closed
            FROM signal_logs
        """))
        row = r.fetchone()
        result["journal_health"] = {
            "total_signals": row[0],
            "with_outcome": row[1],
            "with_exit_price": row[2],
            "executed": row[3],
            "executed_and_closed": row[4],
            "outcome_fill_rate": f"{round(row[1] / row[0] * 100, 1)}%" if row[0] else "0%",
        }
    except Exception as e:
        result["journal_health"] = {"error": str(e)}

    # ── 5. Last labeler run estimate (newest labeled row) ─────────────────────
    try:
        r = await db.execute(text("""
            SELECT MAX(labeled_at) FROM signal_logs WHERE labeled_at IS NOT NULL
        """))
        last = r.scalar()
        result["last_labeler_run"] = str(last) if last else "never"
    except Exception as e:
        result["last_labeler_run"] = {"error": str(e)}

    return result


@app.get("/debug/run-labeler")
async def debug_run_labeler(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from app.models import Credentials
    from app.services.credential_store import decrypt
    result = await db.execute(
        select(Credentials).where(Credentials.broker == "deriv").limit(1)
    )
    cred = result.scalar_one_or_none()
    if not cred:
        return {"error": "no deriv credentials"}
    from ml.outcome_labeler import run_labeler
    asyncio.create_task(run_labeler(decrypt(cred.password)))
    return {"status": "labeler started"}

# ------------------ ROUTES ------------------
app.include_router(api_router)
app.include_router(stream_router)
app.include_router(auth_router)
app.include_router(trade_router)