import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
load_dotenv()

from vestro_backend.app.config import get_settings
from app.db import init_db, AsyncSessionLocal
from app.routes.api import router as api_router, _refresh_firms   # ← add _refresh_firms
from app.routes.stream import router as stream_router
from app.workers.scheduler import create_scheduler

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

    log.info("Pre-warming price cache...")
    await _refresh_firms()                  # ← only addition
    log.info("Price cache ready")

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://vestro-ui.onrender.com",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(stream_router)