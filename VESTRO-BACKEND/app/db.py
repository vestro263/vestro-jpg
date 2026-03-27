from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

settings = get_settings()

# Render PostgreSQL requires SSL — pass via connect_args, not URL query param
_connect_args = {}
if "render.com" in settings.database_url or "ssl=require" in settings.database_url:
    _connect_args = {"ssl": "require"}

_url = (settings.database_url
        .replace("postgresql://", "postgresql+asyncpg://")
        .replace("?ssl=require", "")
        .replace("&ssl=require", ""))

engine = create_async_engine(
    _url,
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