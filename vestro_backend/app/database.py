from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
import os

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
    # Deferred import — avoids circular dependency at module load time.
    # (calibration_loader imports AsyncSessionLocal from this module, so
    #  importing signal_log_model at the top level would create a cycle.)
    from ml.signal_log_model import SignalLogBase  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(SignalLogBase.metadata.create_all)  # new tables