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

@app.get("/debug/tables")
async def list_tables(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))
    return {"tables": [r[0] for r in result.fetchall()]}


# ------------------ ROUTES ------------------
app.include_router(api_router)
app.include_router(stream_router)
app.include_router(auth_router)
app.include_router(trade_router)