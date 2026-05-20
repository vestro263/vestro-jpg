from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
import os

DATABASE_URL = os.environ["DATABASE_URL"]

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DATABASE_URL = (
    DATABASE_URL
    .replace("postgresql://", "postgresql+asyncpg://")
    .replace("?ssl=require", "")
    .replace("&ssl=require", "")
)

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
        # Create all registered tables
        await conn.run_sync(Base.metadata.create_all)

        migrations = [
            # ── Credentials schema ────────────────────────────
            """
            ALTER TABLE credentials
            ADD COLUMN IF NOT EXISTS google_user_id VARCHAR
            """,

            """
            CREATE INDEX IF NOT EXISTS ix_cred_google_user_id
            ON credentials(google_user_id)
            """,

            """
            ALTER TABLE credentials
            ADD COLUMN IF NOT EXISTS account_id VARCHAR
            """,

            """
            CREATE INDEX IF NOT EXISTS ix_cred_account_id
            ON credentials(account_id)
            """,

            """
            ALTER TABLE credentials
            ADD COLUMN IF NOT EXISTS is_demo BOOLEAN DEFAULT FALSE
            """,

            # ── Safe backfill ─────────────────────────────────
            """
            UPDATE credentials
            SET account_id = login
            WHERE account_id IS NULL
              AND login IS NOT NULL
            """,

            """
            UPDATE credentials
            SET is_demo = (
                account_id LIKE 'VRT%%'
                OR account_id LIKE 'CR%%'
            )
            WHERE account_id IS NOT NULL
            """,
        ]

        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception as e:
                print(f"[init_db migration warning] {e}")

        await conn.commit()
        print("✅ DB initialized")