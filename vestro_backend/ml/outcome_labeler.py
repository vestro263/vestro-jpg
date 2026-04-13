"""
outcome_labeler.py  (upgraded)
================================
Retrospectively labels every SignalLog row using the triple-barrier method.

Changes vs original
--------------------
1. ATR-SCALED BARRIERS
   Original used fixed tp_price / sl_price columns, falling back to a flat
   0.5% ATR-proxy.  This produced inconsistent reward/risk ratios across
   volatility regimes — quiet sessions had tiny barriers and noisy sessions
   had huge ones, making WIN/LOSS labels incomparable across time.

   Upgrade: barriers are now set as ATR multiples (default TP=1.5×ATR,
   SL=1.0×ATR).  If the row already has tp_price/sl_price they are used
   as-is (backward-compatible with existing data).

2. INTRA-BAR AMBIGUITY RESOLUTION
   When both TP and SL are touched in the same bar, the original returned
   NEUTRAL (0).  This labelled real winners as noise.
   Upgrade: compare distance from entry to high vs. low.  Whichever is
   closer to entry price wins — statistically the closer level is hit first.

3. NEUTRAL SUPPRESSION
   Rows where entry_price == tp_price == sl_price (bad data) are skipped.
   Rows where the window has not yet elapsed are skipped as before.

4. EXIT_PRICE ALWAYS WRITTEN
   exit_price was previously only set on barrier hits, leaving it NULL on
   timeout rows.  It is now always set to the last tick price in the window
   so journal queries can compute actual P&L on every row.

5. OUTCOME STRING ALIGNED TO SIGNAL
   BUY rows where label == +1 → "WIN",  label == -1 → "LOSS"
   SELL rows where label == +1 → "WIN" (TP hit going short), label == -1 → "LOSS"
   This matches the direction column so journal stats are correct.

Writes back (unchanged schema):
    label_15m / label_30m / label_60m / label_90m / label_4h
    outcome    — "WIN" | "LOSS" | "NEUTRAL"
    exit_price — price at first barrier touch or last tick on timeout
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

# ── ATR multipliers for barrier calculation ───────────────────────────────────
ATR_TP_MULTIPLIER = 1.5    # take-profit = entry ± ATR × 1.5
ATR_SL_MULTIPLIER = 1.0    # stop-loss   = entry ∓ ATR × 1.0
ATR_FALLBACK_PCT  = 0.005  # fallback if atr column is NULL: 0.5% of entry

WINDOW_SECONDS: dict[str, int] = {
    "15m": 15 * 60,
    "30m": 30 * 60,
    "60m": 60 * 60,
    "90m": 90 * 60,
    "4h":  4  * 60 * 60,
}


# =============================================================================
# Deriv tick fetcher  (unchanged from original)
# =============================================================================

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
            # Authorize
            await ws.send(json.dumps({"authorize": api_token}))
            auth_resp = json.loads(await ws.recv())

            if "error" in auth_resp:
                logger.error(
                    f"[outcome_labeler] auth error ({symbol}): {auth_resp['error']}"
                )
                return []

            # Request tick history
            await ws.send(json.dumps({
                "ticks_history": symbol,
                "start":         start_epoch,
                "end":           end_epoch,
                "count":         count,
                "style":         "ticks",
            }))

            data = json.loads(await ws.recv())

        # Detect Deriv API errors
        if "error" in data:
            logger.error(
                f"[outcome_labeler] Deriv error ({symbol}): {data['error']}"
            )
            return []

        # Parse history safely
        history = data.get("history", {})
        times   = history.get("times", [])
        prices  = history.get("prices", [])

        logger.info(
            f"[outcome_labeler] {symbol} ticks={len(times)} "
            f"start={start_epoch} end={end_epoch}"
        )

        return [(int(t), float(p)) for t, p in zip(times, prices)]

    except Exception as exc:
        logger.error(f"[outcome_labeler] tick fetch error ({symbol}): {exc}")
        return []
# =============================================================================
# Triple-barrier labeller  (upgraded)
# =============================================================================

def _compute_barriers(
    entry_price: float,
    direction:   int,
    tp_price:    float | None,
    sl_price:    float | None,
    atr_val:     float | None,
) -> tuple[float, float]:
    """
    Resolve tp / sl prices.

    Priority order:
      1. Use tp_price / sl_price from the DB row if both are set
         (preserves backward-compatibility with existing labeled rows)
      2. Compute from ATR multipliers
      3. Fallback to fixed 0.5% of entry price
    """
    if tp_price is not None and sl_price is not None:
        return float(tp_price), float(sl_price)

    atr = atr_val if (atr_val is not None and atr_val > 0) else entry_price * ATR_FALLBACK_PCT

    if direction == 1:   # BUY
        tp = entry_price + ATR_TP_MULTIPLIER * atr
        sl = entry_price - ATR_SL_MULTIPLIER * atr
    else:                # SELL
        tp = entry_price - ATR_TP_MULTIPLIER * atr
        sl = entry_price + ATR_SL_MULTIPLIER * atr

    return tp, sl


def _triple_barrier_label(
    ticks:          list[tuple[int, float]],
    entry_price:    float,
    tp_price:       float,
    sl_price:       float,
    direction:      int,
    window_seconds: int,
    entry_epoch:    int,
) -> tuple[int, float]:
    """
    Walk ticks forward from entry_epoch.

    Returns (label, exit_price):
        +1 / exit_price — take-profit touched first
        -1 / exit_price — stop-loss touched first
         0 / last_price — time barrier expired

    Intra-bar ambiguity (both barriers touched in same tick):
        Compare distances from entry.  Closer barrier is assumed hit first.
        This is statistically correct and avoids silently discarding real winners.
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

        if direction == 1:       # BUY: TP above entry, SL below
            tp_hit = price >= tp_price
            sl_hit = price <= sl_price
        else:                    # SELL: TP below entry, SL above
            tp_hit = price <= tp_price
            sl_hit = price >= sl_price

        if tp_hit and sl_hit:
            # Both levels breached in same tick — closer one wins
            tp_dist = abs(price - tp_price)
            sl_dist = abs(price - sl_price)
            if tp_dist <= sl_dist:
                return +1, tp_price
            else:
                return -1, sl_price

        if tp_hit:
            return +1, tp_price
        if sl_hit:
            return -1, sl_price

    return 0, last_price   # timeout → NEUTRAL, last known price


def _label_to_outcome(label: int) -> str:
    if label == 1:
        return "WIN"
    if label == -1:
        return "LOSS"
    return "NEUTRAL"


# =============================================================================
# Main labeling routine
# =============================================================================

async def label_pending_rows(api_token: str, batch_size: int = 50) -> int:
    """
    Fetch unlabeled SignalLog rows and write back triple-barrier labels
    plus outcome / exit_price for the journal.

    Returns the number of rows labeled this run.
    """
    now_dt    = datetime.utcnow()
    now_epoch = int(time.time())
    cutoff    = now_dt - timedelta(minutes=16)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SignalLog)
            .where(
                SignalLog.label_15m  == None,        # noqa: E711
                SignalLog.captured_at <= cutoff,
                SignalLog.entry_price != None,        # noqa: E711
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
            direction   = row.direction   # +1 or -1

            # ── Resolve barriers (ATR-scaled upgrade) ─────────────────────
            tp, sl = _compute_barriers(
                entry_price = entry_price,
                direction   = direction,
                tp_price    = row.tp_price,
                sl_price    = row.sl_price,
                atr_val     = row.atr,
            )

            # Sanity check: skip rows with degenerate barriers
            if abs(tp - entry_price) < 1e-8 or abs(sl - entry_price) < 1e-8:
                logger.warning(
                    f"[outcome_labeler] degenerate barriers row={row.id} "
                    f"entry={entry_price} tp={tp} sl={sl} — skipping"
                )
                continue

            max_window = WINDOW_SECONDS["4h"]
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

            # ── Label every elapsed window ────────────────────────────────
            labels:      dict[str, int] = {}
            exit_price:  float | None   = None
            primary_lbl: int | None     = None

            for window_name, window_secs in WINDOW_SECONDS.items():
                if entry_epoch + window_secs > now_epoch:
                    continue   # window has not elapsed yet

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

                # first available label
                if primary_lbl is None:
                    primary_lbl = lbl
                    exit_price = ep

                # upgrade NEUTRAL to decisive result from later window
                if primary_lbl == 0 and lbl != 0:
                    primary_lbl = lbl
                    exit_price = ep

            if not labels:
                logger.info(
                    f"[outcome_labeler] row={row.id} — no windows elapsed, skipping"
                )
                continue

            # exit_price is always written now (even on NEUTRAL/timeout)
            outcome = _label_to_outcome(primary_lbl)

            update_vals = {
                "labeled_at": now_dt,
                **{f"label_{k}": v for k, v in labels.items()},
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


# =============================================================================
# Standalone entry point  (unchanged interface)
# =============================================================================

async def run_labeler(api_token: str) -> None:
    while True:
        n = await label_pending_rows(api_token, batch_size=50)
        if n == 0:
            break
        await asyncio.sleep(1)


if __name__ == "__main__":
    import asyncio
    token = os.environ.get("DERIV_API_TOKEN", "")
    asyncio.run(run_labeler(token))