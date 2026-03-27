import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
load_dotenv()

from app.config import get_settings
from app.db import init_db, AsyncSessionLocal
from app.routes.api import router as api_router
from app.routes.stream import router as stream_router
from app.workers.scheduler import create_scheduler
from app.scrapers import news
from app.pipeline.scorer import score_all_firms

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
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(stream_router)
