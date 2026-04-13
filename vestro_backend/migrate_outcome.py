# fix_signal_logs_account_id.py
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Normalize URL for sync SQLAlchemy
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


def run():
    engine = create_engine(DATABASE_URL, echo=False)

    with engine.begin() as conn:
        # 1. Add account_id column
        conn.execute(text("""
            ALTER TABLE signal_logs
            ADD COLUMN IF NOT EXISTS account_id VARCHAR
        """))

        # 2. Optional index for faster journal queries
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_signal_logs_account_id
            ON signal_logs(account_id)
        """))

        # 3. Backfill existing rows using credentials/user_id if known
        # (edit this line if you want one default account)
        conn.execute(text("""
            UPDATE signal_logs
            SET account_id = 'default_account'
            WHERE account_id IS NULL
        """))

        print("signal_logs updated successfully.")
        print("- added account_id column")
        print("- created index")
        print("- backfilled null rows")

    engine.dispose()


if __name__ == "__main__":
    run()