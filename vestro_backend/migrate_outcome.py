"""
migrate_active_account.py
=========================
Fixes: column "active_account" is of type boolean but expression
       is of type character varying

Run once:
    py migrate_active_account.py
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
        print("\n── Checking users table columns ──")
        result = conn.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'users'
            ORDER BY ordinal_position
        """))
        for row in result.fetchall():
            print(f"  {row[0]:25s} {row[1]}")

        print("\n── Fixing active_account column ──")

        # Option 1: Drop the column if it's not needed
        # conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS active_account"))

        # Option 2: Change type to VARCHAR to match what's being inserted
        conn.execute(text("""
            ALTER TABLE users
            ALTER COLUMN active_account TYPE VARCHAR
            USING active_account::VARCHAR
        """))
        print("✓ active_account changed to VARCHAR")

        print("\n── Final users table schema ──")
        result = conn.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'users'
            ORDER BY ordinal_position
        """))
        for row in result.fetchall():
            print(f"  {row[0]:25s} {row[1]}")

    engine.dispose()
    print("\n✓ Done")


if __name__ == "__main__":
    run()