"""
outcome_labeler.py  (upgraded)
================================
Added label_1d and label_3d windows for Gold (frxXAUUSD).
Synthetic indices (R_75, R_25, CRASH500) skip these windows —
Deriv tick history only goes ~3 days for synthetics.
"""

from __future__ import annotations

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
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "")

ATR_TP_MULTIPLIER = 1.5
ATR_SL_MULTIPLIER = 1.0
ATR_FALLBACK_PCT  = 0.005

# Symbols that support longer windows (real assets with deep tick history)
SWING_SYMBOLS = {"frxXAUUSD", "frxEURUSD", "frxGBPUSD"}

WINDOW_SECONDS: dict[str, int] = {
    "15m": 15 * 60,
    "30m": 30 * 60,
    "60m": 60 * 60,
    "90m": 90 * 60,
    "4h":  4  * 60 * 60,
    "1d":  24 * 60 * 60,   # Gold/forex only
    "3d":  72 * 60 * 60,   # Gold/forex only
}

# Windows available per symbol type
SYNTHETIC_WINDOWS = {"15m", "30m", "60m", "90m", "4h"}
SWING_WINDOWS     = {"15m", "30m", "60m", "90m", "4h", "1d", "3d"}


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
            auth_resp = json.loads(await ws.recv())
            if "error" in auth_resp:
                logger.error(f"[outcome_labeler] auth error ({symbol}): {auth_resp['error']}")
                return []

            await ws.send(json.dumps({
                "ticks_history": symbol,
                "start":         start_epoch,
                "end":           end_epoch,
                "count":         count,
                "style":         "ticks",
            }))
            data = json.loads(await ws.recv())

        if "error" in data:
            logger.error(f"[outcome_labeler] Deriv error ({symbol}): {data['error']}")
            return []

        history = data.get("history", {})
        times   = history.get("times",  [])
        prices  = history.get("prices", [])

        logger.info(
            f"[outcome_labeler] {symbol} ticks={len(times)} "
            f"start={start_epoch} end={end_epoch}"
        )
        return [(int(t), float(p)) for t, p in zip(times, prices)]

    except Exception as exc:
        logger.error(f"[outcome_labeler] tick fetch error ({symbol}): {exc}")
        return []


def _compute_barriers(
    entry_price: float,
    direction:   int,
    tp_price:    float | None,
    sl_price:    float | None,
    atr_val:     float | None,
) -> tuple[float, float]:
    if tp_price is not None and sl_price is not None:
        return float(tp_price), float(sl_price)
    atr = atr_val if (atr_val is not None and atr_val > 0) else entry_price * ATR_FALLBACK_PCT
    if direction == 1:
        return entry_price + ATR_TP_MULTIPLIER * atr, entry_price - ATR_SL_MULTIPLIER * atr
    return entry_price - ATR_TP_MULTIPLIER * atr, entry_price + ATR_SL_MULTIPLIER * atr


def _triple_barrier_label(
    ticks:          list[tuple[int, float]],
    entry_price:    float,
    tp_price:       float,
    sl_price:       float,
    direction:      int,
    window_seconds: int,
    entry_epoch:    int,
) -> tuple[int, float]:
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

        if direction == 1:
            tp_hit = price >= tp_price
            sl_hit = price <= sl_price
        else:
            tp_hit = price <= tp_price
            sl_hit = price >= sl_price

        if tp_hit and sl_hit:
            tp_dist = abs(price - tp_price)
            sl_dist = abs(price - sl_price)
            return (+1, tp_price) if tp_dist <= sl_dist else (-1, sl_price)
        if tp_hit:
            return +1, tp_price
        if sl_hit:
            return -1, sl_price

    return 0, last_price


def _label_to_outcome(label: int) -> str:
    return {1: "WIN", -1: "LOSS"}.get(label, "NEUTRAL")


async def label_pending_rows(api_token: str, batch_size: int = 50) -> int:
    now_dt    = datetime.utcnow()
    now_epoch = int(time.time())
    cutoff    = now_dt - timedelta(minutes=16)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SignalLog)
            .where(
                SignalLog.label_15m  == None,
                SignalLog.captured_at <= cutoff,
                SignalLog.entry_price != None,
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
            entry_epoch = int(
                (row.captured_at - datetime(1970, 1, 1)).total_seconds()
            )
            entry_price = row.entry_price
            direction   = row.direction

            tp, sl = _compute_barriers(
                entry_price = entry_price,
                direction   = direction,
                tp_price    = row.tp_price,
                sl_price    = row.sl_price,
                atr_val     = row.atr,
            )

            if abs(tp - entry_price) < 1e-8 or abs(sl - entry_price) < 1e-8:
                logger.warning(
                    f"[outcome_labeler] degenerate barriers row={row.id} "
                    f"entry={entry_price} tp={tp} sl={sl} — skipping"
                )
                continue

            # Determine which windows apply to this symbol
            is_swing  = row.symbol in SWING_SYMBOLS
            windows   = SWING_WINDOWS if is_swing else SYNTHETIC_WINDOWS

            max_window = WINDOW_SECONDS["3d"] if is_swing else WINDOW_SECONDS["4h"]
            end_epoch  = min(entry_epoch + max_window + 60, now_epoch)

            ticks = await _fetch_ticks_range(
                api_token   = api_token,
                symbol      = row.symbol,
                start_epoch = entry_epoch,
                end_epoch   = end_epoch,
                count       = 5000,
            )

            if not ticks:
                logger.warning(
                    f"[outcome_labeler] no ticks for {row.symbol} row={row.id}"
                )
                continue

            labels:      dict[str, int] = {}
            exit_price:  float | None   = None
            primary_lbl: int | None     = None

            for window_name, window_secs in WINDOW_SECONDS.items():
                # Skip windows not applicable to this symbol
                if window_name not in windows:
                    continue
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

                if primary_lbl is None:
                    primary_lbl = lbl
                    exit_price  = ep
                if primary_lbl == 0 and lbl != 0:
                    primary_lbl = lbl
                    exit_price  = ep

            if not labels:
                logger.info(f"[outcome_labeler] row={row.id} — no windows elapsed, skipping")
                continue

            outcome = _label_to_outcome(primary_lbl)

            # Build update dict — only include columns that exist
            update_vals: dict = {
                "labeled_at": now_dt,
                "outcome":    outcome,
                "exit_price": exit_price,
            }

            # Standard windows — always exist in schema
            for w in ("15m", "30m", "60m", "90m", "4h"):
                if w in labels:
                    update_vals[f"label_{w}"] = labels[w]

            # Swing windows — only write if columns exist (Gold)
            for w in ("1d", "3d"):
                if w in labels:
                    col = f"label_{w}"
                    # Only write if SignalLog has this column
                    if hasattr(SignalLog, col):
                        update_vals[col] = labels[w]

            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(SignalLog)
                    .where(SignalLog.id == row.id)
                    .values(**update_vals)
                )
                await db.commit()

            labeled += 1
            logger.info(
                "[outcome_labeler] labeled %s:%s dir=%+d labels=%s outcome=%s "
                "exit=%.5f tp=%.5f sl=%.5f row=%s",
                row.strategy, row.symbol, direction,
                labels, outcome,
                exit_price or 0, tp, sl, row.id,
            )

            await asyncio.sleep(0.5)

        except Exception as exc:
            logger.error(f"[outcome_labeler] error on row {row.id}: {exc}")
            continue

    logger.info(f"[outcome_labeler] done — {labeled}/{len(rows)} labeled")
    return labeled


async def run_labeler(api_token: str) -> None:
    while True:
        n = await label_pending_rows(api_token, batch_size=50)
        if n == 0:
            break
        await asyncio.sleep(1)


if __name__ == "__main__":
    token = os.environ.get("DERIV_API_TOKEN", "")
    asyncio.run(run_labeler(token))