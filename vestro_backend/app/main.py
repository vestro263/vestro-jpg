import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
load_dotenv()
from .routes.trade import router as trade_router
import asyncio
import logging
from contextlib import asynccontextmanager
from .database import init_db, AsyncSessionLocal
from .routes.auth import router as auth_router
from .config import get_settings
from .routes.api import router as api_router, _refresh_firms
from .routes.stream import router as stream_router
from .workers.scheduler import create_scheduler
from fastapi.responses import JSONResponse
from fastapi.requests import Request
from .services.signal_engine import run_signal_loop
import traceback


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

    # Start signal engine
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


async def run_signal_loop():
    print("[signal_engine] starting...")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Credentials))
        creds = result.scalars().all()

    print(f"[signal_engine] credentials found: {len(creds)}")  # ← add this

    deriv_cred = next((c for c in creds if c.broker == "deriv"), None)
    print(f"[signal_engine] deriv_cred found: {deriv_cred is not None}")  # ← add this

    if deriv_cred:
        api_token = decrypt(deriv_cred.password)
        print(f"[signal_engine] booting strategy runner...")  # ← add this
        await _boot_strategy_runner(api_token)
    else:
        print("[signal_engine] NO DERIV CREDENTIALS — runner not started")  # ← add this

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