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
            "ALTER TABLE signal_logs ADD COLUMN IF NOT EXISTS regime VARCHAR(20)",
            "ALTER TABLE signal_logs ADD COLUMN IF NOT EXISTS label_1d INTEGER",
            "ALTER TABLE signal_logs ADD COLUMN IF NOT EXISTS label_3d INTEGER",
        ]
        for sql in migrations:
            try:
                conn.execute(text(sql))
                print(f"✅ {sql}")
            except Exception as e:
                print(f"❌ {e}")
        conn.commit()
        print("✅ Done")

if __name__ == "__main__":
    run()