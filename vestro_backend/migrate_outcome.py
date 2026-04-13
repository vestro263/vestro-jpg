"""
migrate_signal_log_columns.py
=============================
Adds missing columns to signal_logs:
    outcome     VARCHAR   — "WIN" | "LOSS" | "NEUTRAL"
    exit_price  FLOAT     — price at barrier touch or window end

Run once:
    py migrate_signal_log_columns.py
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Fix old postgres URL format
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Convert asyncpg URL to sync psycopg format for migration
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


def run():
    engine = create_engine(DATABASE_URL, echo=False)

    with engine.begin() as conn:
        print("\n── Current signal_logs columns ──")

        result = conn.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'signal_logs'
            ORDER BY ordinal_position
        """))

        existing = {row[0]: row[1] for row in result.fetchall()}

        for col, dtype in existing.items():
            print(f"  {col:30s} {dtype}")

        print("\n── Adding missing columns ──")

        if "outcome" not in existing:
            conn.execute(text("""
                ALTER TABLE signal_logs
                ADD COLUMN outcome VARCHAR
            """))
            print("✓ outcome column added")
        else:
            print("— outcome already exists, skipping")

        if "exit_price" not in existing:
            conn.execute(text("""
                ALTER TABLE signal_logs
                ADD COLUMN exit_price DOUBLE PRECISION
            """))
            print("✓ exit_price column added")
        else:
            print("— exit_price already exists, skipping")

        print("\n── Final signal_logs schema ──")

        result = conn.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'signal_logs'
            ORDER BY ordinal_position
        """))

        for row in result.fetchall():
            print(f"  {row[0]:30s} {row[1]}")

    engine.dispose()
    print("\n✓ Done")


if __name__ == "__main__":
    run()