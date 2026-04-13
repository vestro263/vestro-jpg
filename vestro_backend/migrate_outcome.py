import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
url = os.getenv("DATABASE_URL","")

if url.startswith("postgres://"):
    url = url.replace("postgres://","postgresql://",1)
if url.startswith("postgresql+asyncpg://"):
    url = url.replace("postgresql+asyncpg://","postgresql://",1)

engine = create_engine(url)

with engine.begin() as conn:
    rows = conn.execute(text("""
        SELECT u.email, u.id, c.user_id AS account_id, c.broker
        FROM users u
        JOIN credentials c
          ON c.google_user_id = u.id
        ORDER BY u.email
    """)).fetchall()

    for r in rows:
        print(f"{r.email} -> {r.account_id} ({r.broker})")