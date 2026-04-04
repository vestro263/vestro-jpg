import asyncio
import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.requests import Request
from dotenv import load_dotenv
load_dotenv()

from .database import init_db
from .config import get_settings
from .routes.api import router as api_router, _refresh_firms
from .routes.stream import router as stream_router
from .routes.auth import router as auth_router
from .routes.trade import router as trade_router
from .workers.scheduler import create_scheduler
from .services.signal_engine import run_signal_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Vestro backend starting up")
    await init_db()
    log.info("DB tables ready")

    await _refresh_firms()
    log.info("Price cache ready")

    asyncio.create_task(run_signal_loop())
    log.info("Signal engine running")

    scheduler = create_scheduler()
    scheduler.start()
    log.info("Scheduler running")
    yield
    scheduler.shutdown(wait=False)
    log.info("Vestro backend shut down")


app = FastAPI(
    title="Vestro Valuation Engine",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)

@app.exception_handler(Exception)
async def debug_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "traceback": traceback.format_exc()}
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://r3bel-production.up.railway.app",
        "https://vestro-ui.onrender.com",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(stream_router)
app.include_router(auth_router)
app.include_router(trade_router)