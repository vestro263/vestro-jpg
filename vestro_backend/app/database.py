from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
import os

from .ml.signal_log_model import SignalLog, CalibrationConfig, SignalLogBase

DATABASE_URL = os.environ["DATABASE_URL"]

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DATABASE_URL = (DATABASE_URL
    .replace("postgresql://", "postgresql+asyncpg://")
    .replace("?ssl=require", "")
    .replace("&ssl=require", ""))

_connect_args = {}
if "render.com" in DATABASE_URL:
    _connect_args = {"ssl": "require"}

engine = create_async_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # NEW — creates signal_logs + calibration_config tables
        from .ml.signal_log_model import SignalLogBase
        await conn.run_sync(SignalLogBase.metadata.create_all)