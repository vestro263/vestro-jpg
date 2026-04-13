import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)

with engine.begin() as conn:
    conn.execute(text("""
        ALTER TABLE credentials
        ADD COLUMN IF NOT EXISTS account_id TEXT;
    """))

    conn.execute(text("""
        ALTER TABLE credentials
        ADD COLUMN IF NOT EXISTS is_demo BOOLEAN DEFAULT false;
    """))

    conn.execute(text("""
        ALTER TABLE credentials
        ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true;
    """))

print("✅ credentials schema fixed")