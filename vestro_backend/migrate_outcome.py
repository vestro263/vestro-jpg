import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

print(f"DEBUG DATABASE_URL = {DATABASE_URL}")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL missing")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql://",
        1
    )

# IMPORTANT: use psycopg sync driver for scripts
SYNC_DB_URL = DATABASE_URL.replace(
    "postgresql+asyncpg://",
    "postgresql://"
)

engine = create_engine(
    SYNC_DB_URL,
    pool_pre_ping=True,
    echo=True,
)


def run():
    with engine.connect() as conn:
        print("\n=== CONNECTED ===")

        result = conn.execute(text("""
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
        """))

        tables = [row[0] for row in result]

        print("\n=== TABLES ===")

        if not tables:
            print("No tables found")
        else:
            for t in tables:
                print(f"✅ {t}")

        print("\n✅ Done")


if __name__ == "__main__":
    run()