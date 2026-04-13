from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        migrations = [
            # ── Existing ─────────────────────────────────────────────────────
            "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS google_user_id VARCHAR",
            "CREATE INDEX IF NOT EXISTS ix_cred_google_user_id ON credentials(google_user_id)",

            # ── New ──────────────────────────────────────────────────────────
            "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS account_id VARCHAR",
            "CREATE INDEX IF NOT EXISTS ix_cred_account_id ON credentials(account_id)",

            # Backfill: copy legacy user_id → account_id, derive is_demo
            # Idempotent — WHERE account_id IS NULL means it only runs on unset rows
            """
            UPDATE credentials
            SET   account_id = user_id,
                  is_demo    = (user_id LIKE 'VRT%')
            WHERE account_id IS NULL
              AND user_id    IS NOT NULL
            """,
        ]

        for sql in migrations:
            await conn.execute(text(sql))