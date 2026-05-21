import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

print(f"DEBUG DATABASE_URL = {DATABASE_URL}")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL missing")

# ------------------------------------------------------------
# Normalize postgres scheme
# ------------------------------------------------------------

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql://",
        1,
    )

# ------------------------------------------------------------
# Force sync psycopg driver for scripts
# ------------------------------------------------------------

SYNC_DB_URL = DATABASE_URL.replace(
    "postgresql+asyncpg://",
    "postgresql://",
)

print(f"DEBUG SYNC_DB_URL = {SYNC_DB_URL}")

engine = create_engine(
    SYNC_DB_URL,
    pool_pre_ping=True,
    echo=True,
)

# ------------------------------------------------------------
# Detect demo accounts properly
# ------------------------------------------------------------

def detect_demo(account_id: str) -> bool:
    """
    Deriv account detection.

    DEMO:
        VRTC...
        DT...
        DOT...

    REAL:
        CR...
        MF...
        etc
    """

    if not account_id:
        return False

    account_id = account_id.upper().strip()

    return (
        account_id.startswith("VRTC")
        or account_id.startswith("DT")
        or account_id.startswith("DOT")
    )


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def run():
    with engine.begin() as conn:

        print("\n=== CONNECTED ===")

        # ----------------------------------------------------
        # Tables
        # ----------------------------------------------------

        result = conn.execute(text("""
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
        """))

        tables = [row[0] for row in result]

        print("\n=== TABLES ===")

        if not tables:
            print("No tables found")
        else:
            for t in tables:
                print(f"✅ {t}")

        # ----------------------------------------------------
        # Credentials audit
        # ----------------------------------------------------

        print("\n=== CREDENTIAL AUDIT ===")

        creds = conn.execute(text("""
            SELECT
                id,
                broker,
                account_id,
                is_demo
            FROM credentials
            ORDER BY id
        """)).fetchall()

        if not creds:
            print("No credentials found")
            return

        updated = 0

        for row in creds:
            row_id     = row[0]
            broker     = row[1]
            account_id = row[2]
            db_demo    = row[3]

            actual_demo = detect_demo(account_id)

            status = "OK"

            if db_demo != actual_demo:
                status = "FIXED"

                conn.execute(text("""
                    UPDATE credentials
                    SET is_demo = :is_demo
                    WHERE id = :id
                """), {
                    "id": row_id,
                    "is_demo": actual_demo,
                })

                updated += 1

            print(
                f"[{status}] "
                f"id={row_id} "
                f"broker={broker} "
                f"account={account_id} "
                f"db_demo={db_demo} "
                f"actual_demo={actual_demo}"
            )

        # ----------------------------------------------------
        # Summary
        # ----------------------------------------------------

        print("\n=== SUMMARY ===")

        print(f"Updated rows: {updated}")

        demo_accounts = conn.execute(text("""
            SELECT account_id
            FROM credentials
            WHERE is_demo = true
            ORDER BY account_id
        """)).fetchall()

        print("\nDemo accounts:")

        for row in demo_accounts:
            print(f"✅ {row[0]}")

            # ----------------------------------------------------
            # Token preview
            # ----------------------------------------------------

            print("\n=== TOKEN PREVIEW ===")

            tokens = conn.execute(text("""
                    SELECT account_id, LEFT(password, 15) as preview, LENGTH(password) as length
                    FROM credentials
                    ORDER BY account_id
                """)).fetchall()

            for row in tokens:
                print(
                    f"account={row[0]} "
                    f"token_preview={row[1]} "
                    f"token_length={row[2]}"
                )

        print("\n✅ Done")


if __name__ == "__main__":
    run()