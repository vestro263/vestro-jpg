# inspect_gold_rows.py
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

    sql = """
    SELECT strategy, symbol, signal, direction, entry_price, atr,
           tp_price, sl_price, outcome, exit_price, captured_at
    FROM signal_logs
    WHERE symbol = 'frxXAUUSD'
      AND signal != 'HOLD'
    ORDER BY captured_at DESC
    LIMIT 20
    """

    with engine.begin() as conn:
        rows = conn.execute(text(sql)).fetchall()

        if not rows:
            print("No Gold rows found.")
            return

        for r in rows:
            print(f"""
        strategy    : {r.strategy}
        symbol      : {r.symbol}
        signal      : {r.signal}
        direction   : {r.direction}
        entry_price : {r.entry_price}
        atr         : {r.atr}
        tp_price    : {r.tp_price}
        sl_price    : {r.sl_price}
        outcome     : {r.outcome}
        exit_price  : {r.exit_price}
        captured_at : {r.captured_at}
        {'-' * 120}
        """)

    engine.dispose()


if __name__ == "__main__":
    run()