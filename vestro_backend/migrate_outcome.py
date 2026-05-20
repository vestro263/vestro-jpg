import os
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import create_engine, text

engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    tables = conn.execute(text("""
        SELECT tablename
        FROM pg_tables
        WHERE schemaname='public'
        ORDER BY tablename
    """))

    for t in tables:
        print(t[0])