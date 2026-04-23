import os
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

from sqlalchemy import create_engine, text
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

def run():
    with engine.connect() as conn:
        migrations = [
            "ALTER TABLE calibration_config ADD COLUMN IF NOT EXISTS rsi_buy_min FLOAT",
            "ALTER TABLE calibration_config ADD COLUMN IF NOT EXISTS rsi_sell_max FLOAT",
        ]
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"✅ {sql}")
            except Exception as e:
                conn.rollback()
                print(f"❌ {e}")
        print("✅ Done")

if __name__ == "__main__":
    run()