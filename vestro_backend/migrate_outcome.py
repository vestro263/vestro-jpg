import os
from dotenv import load_dotenv

# ─────────────────────────────
# LOAD ENV FIRST (CRITICAL FIX)
# ─────────────────────────────
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ─────────────────────────────
# NOW SAFE TO IMPORT APP
# ─────────────────────────────
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Credentials


engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine)


def is_demo(login: str | None) -> bool:
    if not login:
        return False
    return login.upper().startswith(("VRTC", "VRT", "DMT"))


def run():
    db = SessionLocal()

    try:
        rows = db.query(Credentials).all()

        for r in rows:
            if not r.login:
                r.login = getattr(r, "user_id", None)

            r.is_demo = is_demo(r.login)

            if getattr(r, "is_active", None) is None:
                r.is_active = True

        db.commit()

        print("✅ Migration complete")

    except Exception as e:
        db.rollback()
        print(f"❌ Migration failed: {e}")
        raise

    finally:
        db.close()


if __name__ == "__main__":
    run()