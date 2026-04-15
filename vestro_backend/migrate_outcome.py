import os
from dotenv import load_dotenv

# ─────────────────────────────
# LOAD ENV FIRST
# ─────────────────────────────
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ─────────────────────────────
# SAFE IMPORTS AFTER ENV LOAD
# ─────────────────────────────
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import User

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


def run():
    db = SessionLocal()

    try:
        rows = db.query(User.id, User.email).all()

        if not rows:
            print("⚠️ No users found")
            return

        print("\n📌 USERS TABLE")
        print("-" * 50)

        for user_id, email in rows:
            print(f"ID: {user_id} | Email: {email}")

        print("-" * 50)
        print(f"✅ Total Users: {len(rows)}")

    except Exception as e:
        print(f"❌ Query failed: {e}")
        raise

    finally:
        db.close()


if __name__ == "__main__":
    run()