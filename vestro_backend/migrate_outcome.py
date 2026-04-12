"""
migrate_calibration.py
======================
Run once to add missing RSI columns to calibration_config table.

Usage:
    py migrate_calibration.py
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if "postgresql+asyncpg://" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


def run():
    engine = create_engine(DATABASE_URL, echo=False)

    with engine.begin() as conn:
        print("\n── Adding missing columns to calibration_config ──")

        cols = [
            ("rsi_buy_min",  "FLOAT"),
            ("rsi_buy_max",  "FLOAT"),
            ("rsi_sell_min", "FLOAT"),
            ("rsi_sell_max", "FLOAT"),
        ]

        for col, dtype in cols:
            conn.execute(text(f"""
                ALTER TABLE calibration_config
                ADD COLUMN IF NOT EXISTS {col} {dtype} DEFAULT NULL
            """))
            print(f"  ✓ {col}")

        print("\n── Verification ──")
        result = conn.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'calibration_config'
            ORDER BY ordinal_position
        """))
        for row in result.fetchall():
            print(f"  {row[0]:30s} {row[1]}")

    engine.dispose()
    print("\n✓ Done")


if __name__ == "__main__":
    run()