"""
outcome_labeler.py
==================
Retrospectively labels every SignalLog row using the triple-barrier method.

Writes back:
  label_15m / label_30m / label_60m / label_90m / label_4h  — ML training labels
  outcome    — "WIN" | "LOSS" | "NEUTRAL"   ← used by /api/journal
  exit_price — price at barrier touch or window end           ← used by journal
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta

import websockets
from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from .signal_log_model import SignalLog

logger       = logging.getLogger(__name__)
DERIV_APP_ID = os.environ["DERIV_APP_ID"]

WINDOW_TICKS = {
    "15m": 900,
    "30m": 1800,
    "60m": 3600,
    "90m": 5000,
    "4h":  5000,
}

WINDOW_SECONDS = {
    "15m": 15 * 60,
    "30m": 30 * 60,
    "60m": 60 * 60,
    "90m": 90 * 60,
    "4h":  4 * 60 * 60,
}


# ── Deriv tick fetcher ────────────────────────────────────────────────────────

async def _fetch_ticks_range(
    api_token:   str,
    symbol:      str,
    start_epoch: int,
    end_epoch:   int,
    count:       int = 5000,
) -> list[tuple[int, float]]:
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


# ── Triple-barrier labeler ────────────────────────────────────────────────────
# Returns (label, exit_price) instead of just label

def _triple_barrier_label(
    ticks:          list[tuple[int, float]],
    entry_price:    float,
    tp_price:       float,
    sl_price:       float,
    direction:      int,        # +1 BUY, -1 SELL
    window_seconds: int,
    entry_epoch:    int,
) -> tuple[int, float]:
    """
    Walk ticks forward from entry_epoch.
    Returns (label, exit_price):
      label = +1 TP hit, -1 SL hit, 0 timeout
      exit_price = price at the moment the barrier was hit (or last tick on timeout)
    """
    if not ticks or entry_price is None:
        return 0, entry_price

    deadline   = entry_epoch + window_seconds
    last_price = entry_price

    for epoch, price in ticks:
        if epoch < entry_epoch:
            continue
        if epoch > deadline:
            break

        last_price = price

        if direction == 1:          # BUY — TP above, SL below
            if price >= tp_price:
                return +1, price
            if price <= sl_price:
                return -1, price
        else:                       # SELL — TP below, SL above
            if price <= tp_price:
                return +1, price
            if price >= sl_price:
                return -1, price

    return 0, last_price            # timeout — neutral, last seen price


# ── Label → outcome string ────────────────────────────────────────────────────

def _label_to_outcome(label: int) -> str:
    if label == 1:
        return "WIN"
    if label == -1:
        return "LOSS"
    return "NEUTRAL"


# ── Main labeling routine ─────────────────────────────────────────────────────

async def label_pending_rows(api_token: str, batch_size: int = 50) -> int:
    """
    Fetch unlabeled SignalLog rows and write back triple-barrier labels
    PLUS outcome / exit_price so the journal can display them.
    Returns the number of rows labeled this run.
    """
    now        = datetime.utcnow()
    now_epoch  = int(time.time())
    cutoff_15m = now - timedelta(minutes=16)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SignalLog)
            .where(
                SignalLog.label_15m  == None,          # noqa: E711
                SignalLog.captured_at <= cutoff_15m,
                SignalLog.entry_price != None,          # noqa: E711
                SignalLog.signal      != "HOLD",
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
            entry_epoch = int((row.captured_at - datetime(1970, 1, 1)).total_seconds())
            symbol      = row.symbol
            entry_price = row.entry_price
            direction   = row.direction     # +1 or -1

            tp = row.tp_price
            sl = row.sl_price

            if tp is None or sl is None:
                atr_val = row.atr or (entry_price * 0.005)
                if direction == 1:
                    tp = entry_price + atr_val
                    sl = entry_price - atr_val
                else:
                    tp = entry_price - atr_val
                    sl = entry_price + atr_val

            max_window = WINDOW_SECONDS["4h"]
            end_epoch  = min(entry_epoch + max_window + 60, now_epoch)

            ticks = await _fetch_ticks_range(
                api_token   = api_token,
                symbol      = symbol,
                start_epoch = entry_epoch,
                end_epoch   = end_epoch,
                count       = 5000,
            )

            if not ticks:
                logger.warning(f"[outcome_labeler] no ticks for {symbol} row={row.id}")
                continue

            # ── Label every elapsed window ────────────────────────────────
            labels      = {}
            exit_price  = None   # will be set from the primary (15m) window
            primary_lbl = None

            for window_name, window_secs in WINDOW_SECONDS.items():
                if entry_epoch + window_secs > now_epoch:
                    continue

                lbl, ep = _triple_barrier_label(
                    ticks          = ticks,
                    entry_price    = entry_price,
                    tp_price       = tp,
                    sl_price       = sl,
                    direction      = direction,
                    window_seconds = window_secs,
                    entry_epoch    = entry_epoch,
                )
                labels[window_name] = lbl

                # Use the 15m window as the primary outcome; fall back to
                # the first window that has elapsed if 15m hasn't yet.
                if window_name == "15m" or primary_lbl is None:
                    primary_lbl = lbl
                    exit_price  = ep

            if not labels:
                logger.info(f"[outcome_labeler] row={row.id} — no windows elapsed yet, skipping")
                continue

            outcome = _label_to_outcome(primary_lbl)

            update_vals = {
                "labeled_at": now,
                # ── ML labels ────────────────────────────────────────────
                **{f"label_{k}": v for k, v in labels.items()},
                # ── Journal fields ────────────────────────────────────────
                "outcome":    outcome,
                "exit_price": exit_price,
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
                "[outcome_labeler] labeled %s:%s signal=%s labels=%s outcome=%s "
                "exit=%.5f row=%s",
                row.strategy, row.symbol, row.signal,
                labels, outcome, exit_price or 0, row.id,
            )

            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"[outcome_labeler] error on row {row.id}: {e}")
            continue

    logger.info(f"[outcome_labeler] done — {labeled}/{len(rows)} labeled")
    return labeled


# ── Standalone entry point ────────────────────────────────────────────────────

async def run_labeler(api_token: str) -> None:
    while True:
        n = await label_pending_rows(api_token, batch_size=50)
        if n == 0:
            break
        await asyncio.sleep(1)


if __name__ == "__main__":
    token = os.environ.get("DERIV_API_TOKEN", "")
    asyncio.run(run_labeler(token))