"""
migrate_outcome.py
==================
Run once to:
  1. Add outcome, exit_price, executed_at columns to signal_logs
  2. Backfill outcome from label_15m

Usage:
    python migrate_outcome.py

Reads DATABASE_URL from your .env automatically.
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Render gives postgres:// — psycopg2 needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Strip asyncpg driver if someone has it set
if "postgresql+asyncpg://" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


def run():
    engine = create_engine(DATABASE_URL, echo=False)

    with engine.begin() as conn:

        print("\n── Step 1: Adding columns (safe, skips if already exist) ──")

        conn.execute(text("""
            ALTER TABLE signal_logs
            ADD COLUMN IF NOT EXISTS outcome     VARCHAR(10) DEFAULT NULL
        """))

        conn.execute(text("""
            ALTER TABLE signal_logs
            ADD COLUMN IF NOT EXISTS exit_price  FLOAT DEFAULT NULL
        """))

        conn.execute(text("""
            ALTER TABLE signal_logs
            ADD COLUMN IF NOT EXISTS executed_at TIMESTAMP DEFAULT NULL
        """))

        print("✓ Columns added\n")

        print("── Step 2: Backfilling outcome from label_15m ──")

        result = conn.execute(text("""
            UPDATE signal_logs
            SET outcome = CASE
                WHEN label_15m =  1 THEN 'WIN'
                WHEN label_15m = -1 THEN 'LOSS'
                WHEN label_15m =  0 THEN 'NEUTRAL'
            END
            WHERE label_15m IS NOT NULL
              AND outcome IS NULL
        """))

        print(f"✓ Backfilled {result.rowcount} rows\n")

        print("── Step 3: Verification ──")

        counts = conn.execute(text("""
            SELECT outcome, COUNT(*) as cnt
            FROM signal_logs
            GROUP BY outcome
            ORDER BY outcome
        """))

        for row in counts.fetchall():
            label = row[0] if row[0] is not None else "NULL (unlabeled)"
            print(f"  {label:12s}: {row[1]:>7,}")

    engine.dispose()
    print("\n✓ Migration complete")


if __name__ == "__main__":
    run()