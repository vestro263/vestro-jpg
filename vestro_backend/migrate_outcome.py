# fix_active_accounts.py
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)

MAPPING = {
    "martinkaduku@gmail.com": "VRTC15325596",
    "walternyika07@gmail.com": "VRW1670605",
    "apoloniachikurumani@gmail.com": "VRTC6214285",
    "winniemanyawu@gmail.com": None,   # choose real account first
}

with engine.begin() as conn:
    for email, acct in MAPPING.items():
        if acct:
            conn.execute(text("""
                UPDATE users
                SET active_account = :acct
                WHERE email = :email
            """), {"acct": acct, "email": email})
            print(f"Updated {email} -> {acct}")

print("Done.")