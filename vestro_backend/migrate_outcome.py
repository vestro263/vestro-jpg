from sqlalchemy import text

async def update_latest_open_signal(
    db,
    strategy: str,
    symbol: str,
    outcome: str,
    exit_price: float,
):
    label = 1 if outcome == "WIN" else (-1 if outcome == "LOSS" else 0)

    await db.execute(text("""
        UPDATE signal_logs
        SET outcome    = :outcome,
            exit_price = :exit_price,
            label_15m  = :label
        WHERE id = (
            SELECT id
            FROM signal_logs
            WHERE strategy = :strategy
              AND symbol = :symbol
              AND executed = true
              AND outcome IS NULL
            ORDER BY captured_at DESC
            LIMIT 1
        )
    """), {
        "strategy": strategy,
        "symbol": symbol,
        "outcome": outcome,
        "exit_price": exit_price,
        "label": label,
    })

    await db.commit()