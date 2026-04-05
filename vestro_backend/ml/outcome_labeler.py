"""
outcome_labeler.py
==================
Retrospectively labels every SignalLog row using the triple-barrier method:

  For a BUY signal at entry_price with ATR-derived SL/TP:
    Scan forward tick-by-tick from entry_time to entry_time + window
    If price hits TP first  → label = +1  (WIN)
    If price hits SL first  → label = -1  (LOSS)
    If time runs out        → label =  0  (neutral / timeout)

Windows labeled (per config):
    15m  — primary metric (used by default in training)
    30m  — primary extended
    60m  — secondary confirmation
    90m  — secondary extended
    4h   — long-term window

Run modes:
    1. Scheduled task — call run_labeler() from your scheduler or signal_loop
    2. Standalone     — python -m app.ml.outcome_labeler

Only rows where:
    - label_15m IS NULL  (not yet labeled)
    - captured_at <= now() - 15 minutes  (enough time has elapsed)
    - entry_price IS NOT NULL            (signal had a price attached)
    - signal != 'HOLD'                   (no point labeling non-signals)
are processed each run.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import websockets
from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from .signal_log_model import SignalLog

logger      = logging.getLogger(__name__)
DERIV_APP_ID = os.environ["DERIV_APP_ID"]

# ── How many ticks to fetch per window (Deriv max = 5000) ─────
WINDOW_TICKS = {
    "15m": 900,    # 15 min × 1 tick/s ≈ 900; fetch 1000 to be safe
    "30m": 1800,
    "60m": 3600,
    "90m": 5000,   # capped at Deriv max
    "4h":  5000,   # capped — will use candles fallback for 4h
}

# ── Time deltas for each window ───────────────────────────────
WINDOW_SECONDS = {
    "15m": 15 * 60,
    "30m": 30 * 60,
    "60m": 60 * 60,
    "90m": 90 * 60,
    "4h":  4 * 60 * 60,
}


# ============================================================
# DERIV TICK FETCHER
# ============================================================

async def _fetch_ticks_range(
    api_token: str,
    symbol: str,
    start_epoch: int,
    end_epoch: int,
    count: int = 5000,
) -> list[tuple[int, float]]:
    """
    Fetch up to `count` ticks for `symbol` between start_epoch and end_epoch.
    Returns list of (epoch_seconds, price).
    """
    url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        async with websockets.connect(url, open_timeout=15) as ws:
            await ws.send(json.dumps({"authorize": api_token}))
            await ws.recv()

            await ws.send(json.dumps({
                "ticks_history": symbol,
                "start":         start_epoch,
                "end":           end_epoch,
                "count":         count,
                "style":         "ticks",
            }))
            data = json.loads(await ws.recv())

        history = data.get("history", {})
        times   = history.get("times",  [])
        prices  = history.get("prices", [])
        return [(int(t), float(p)) for t, p in zip(times, prices)]

    except Exception as e:
        logger.error(f"[outcome_labeler] tick fetch error ({symbol}): {e}")
        return []


# ============================================================
# TRIPLE-BARRIER LABELER
# ============================================================

def _triple_barrier_label(
    ticks: list[tuple[int, float]],
    entry_price: float,
    tp_price: float,
    sl_price: float,
    direction: int,           # +1 BUY, -1 SELL
    window_seconds: int,
    entry_epoch: int,
) -> int:
    """
    Walk ticks forward from entry_epoch.
    Return +1 if TP hit first, -1 if SL hit first, 0 if time expires.
    """
    if not ticks or entry_price is None:
        return 0

    deadline = entry_epoch + window_seconds

    for epoch, price in ticks:
        if epoch < entry_epoch:
            continue
        if epoch > deadline:
            break

        if direction == 1:    # BUY — TP above, SL below
            if price >= tp_price:
                return +1
            if price <= sl_price:
                return -1
        else:                 # SELL — TP below, SL above
            if price <= tp_price:
                return +1
            if price >= sl_price:
                return -1

    return 0   # timeout — neutral


# ============================================================
# MAIN LABELING ROUTINE
# ============================================================

async def label_pending_rows(api_token: str, batch_size: int = 50) -> int:
    """
    Fetch unlabeled SignalLog rows and write back triple-barrier labels.
    Returns the number of rows labeled this run.
    """
    now        = datetime.now(timezone.utc)
    # Only process rows old enough for the 15m window to have elapsed
    cutoff_15m = now - timedelta(minutes=16)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SignalLog)
            .where(
                SignalLog.label_15m == None,          # noqa: E711
                SignalLog.captured_at <= cutoff_15m,
                SignalLog.entry_price != None,         # noqa: E711
                SignalLog.signal != "HOLD",
            )
            .order_by(SignalLog.captured_at)
            .limit(batch_size)
        )
        rows = result.scalars().all()

    if not rows:
        logger.info("[outcome_labeler] no pending rows to label")
        return 0

    logger.info(f"[outcome_labeler] labeling {len(rows)} rows...")
    labeled = 0

    for row in rows:
        try:
            entry_epoch = int(row.captured_at.replace(tzinfo=timezone.utc).timestamp())
            symbol      = row.symbol
            entry_price = row.entry_price
            direction   = row.direction  # +1 or -1

            # Derive TP/SL from stored values; fall back to ATR-based estimate
            tp = row.tp_price
            sl = row.sl_price

            if tp is None or sl is None:
                # Fallback: use 1× ATR distance (stored in atr column)
                atr_val = row.atr or (entry_price * 0.005)  # 0.5% of price
                if direction == 1:
                    tp = entry_price + atr_val
                    sl = entry_price - atr_val
                else:
                    tp = entry_price - atr_val
                    sl = entry_price + atr_val

            # Fetch ticks covering the longest window we need (4h or 90m)
            max_window = WINDOW_SECONDS["4h"]
            end_epoch  = entry_epoch + max_window + 60  # +60s buffer

            ticks = await _fetch_ticks_range(
                api_token   = api_token,
                symbol      = symbol,
                start_epoch = entry_epoch,
                end_epoch   = min(end_epoch, int(now.timestamp())),
                count       = 5000,
            )

            if not ticks:
                logger.warning(f"[outcome_labeler] no ticks for {symbol} row={row.id}")
                continue

            # Label each window using the same tick stream
            labels = {}
            for window_name, window_secs in WINDOW_SECONDS.items():
                # Skip windows that haven't elapsed yet
                if entry_epoch + window_secs > int(now.timestamp()):
                    continue
                labels[window_name] = _triple_barrier_label(
                    ticks          = ticks,
                    entry_price    = entry_price,
                    tp_price       = tp,
                    sl_price       = sl,
                    direction      = direction,
                    window_seconds = window_secs,
                    entry_epoch    = entry_epoch,
                )

            if not labels:
                continue

            update_vals = {
                "labeled_at": now,
                **{f"label_{k}": v for k, v in labels.items()},
            }

            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(SignalLog)
                    .where(SignalLog.id == row.id)
                    .values(**update_vals)
                )
                await db.commit()

            labeled += 1
            logger.info(
                f"[outcome_labeler] labeled {row.strategy}:{row.symbol} "
                f"signal={row.signal} labels={labels} row={row.id}"
            )

            # Small pause to avoid hammering Deriv WS
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"[outcome_labeler] error on row {row.id}: {e}")
            continue

    logger.info(f"[outcome_labeler] done — {labeled}/{len(rows)} labeled")
    return labeled


# ============================================================
# STANDALONE ENTRY POINT
# ============================================================

async def run_labeler(api_token: str) -> None:
    """
    Runs until all currently-pending rows are labeled.
    Called periodically from signal_engine.run_signal_loop().
    """
    while True:
        n = await label_pending_rows(api_token, batch_size=50)
        if n == 0:
            break
        await asyncio.sleep(1)


if __name__ == "__main__":
    import os
    token = os.environ.get("DERIV_API_TOKEN", "")
    asyncio.run(run_labeler(token))