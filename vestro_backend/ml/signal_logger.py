"""
signal_logger.py
================
Writes one SignalLog row per signal computed by any strategy.

USAGE — call from base_strategy.py right after compute_signal() returns:

    from ..ml.signal_logger import log_signal
    ...
    signal = await self.compute_signal(market_data)
    await log_signal(signal, strategy_name=self.NAME)

For Crash500 which overrides run() directly, call it inside run()
right after compute_signal():

    signal = await self.compute_signal(market_data)
    await log_signal(signal, strategy_name=self.NAME)

The logger is fire-and-forget: it never raises, so a DB error never
kills the strategy pipeline.
"""

import logging
from datetime import datetime, timezone

from ..database import AsyncSessionLocal
from .signal_log_model import SignalLog

logger = logging.getLogger(__name__)


def _extract_features(signal: dict, strategy_name: str) -> dict:
    """
    Pull indicator values out of the signal dict that strategies already
    compute and pass to broadcast_fn / return from compute_signal().
    Both V75 and Crash500 pack everything into signal["meta"] and the
    top-level signal dict.
    """
    meta = signal.get("meta", {})
    sig_inner = signal.get("signal", {})   # some callers nest indicators here

    # Direction integer
    raw_signal = signal.get("signal", "HOLD")
    if isinstance(raw_signal, dict):
        # Crash500 overrides run() and constructs its own broadcast dict;
        # in compute_signal() it always returns signal as a string key.
        raw_signal = "HOLD"
    direction = 1 if raw_signal == "BUY" else (-1 if raw_signal == "SELL" else 0)

    return dict(
        strategy    = strategy_name,
        symbol      = signal.get("symbol", "UNKNOWN"),
        signal      = raw_signal,
        direction   = direction,
        entry_price = meta.get("entry") or meta.get("bid"),
        sl_price    = meta.get("sl"),
        tp_price    = meta.get("tp"),
        amount      = signal.get("amount"),

        # ── V75 indicators ────────────────────────────────────
        rsi         = sig_inner.get("rsi")        if isinstance(sig_inner, dict) else None,
        adx         = sig_inner.get("adx")        if isinstance(sig_inner, dict) else None,
        atr         = sig_inner.get("atr")        if isinstance(sig_inner, dict) else meta.get("atr_val"),
        ema_50      = sig_inner.get("ema50")      if isinstance(sig_inner, dict) else None,
        ema_200     = sig_inner.get("ema200")     if isinstance(sig_inner, dict) else None,
        macd_hist   = sig_inner.get("macd_hist")  if isinstance(sig_inner, dict) else None,
        tss_score   = meta.get("tss"),
        checklist   = meta.get("checklist"),
        confidence  = signal.get("confidence"),
        atr_zone    = meta.get("atr_zone"),

        # ── Crash500 extras ───────────────────────────────────
        drop_spike  = meta.get("drop_spike"),
        recovery    = meta.get("recovery"),
        spike_score = meta.get("score"),

        # Outcome — filled later by outcome_labeler.py
        label_15m   = None,
        label_30m   = None,
        label_60m   = None,
        label_90m   = None,
        label_4h    = None,
        labeled_at  = None,
        executed    = False,
    )


async def log_signal(signal: dict, strategy_name: str) -> str | None:
    """
    Persist one SignalLog row.  Returns the new row's id, or None on error.
    Never raises — logging failures must not kill the strategy pipeline.
    """
    try:
        fields = _extract_features(signal, strategy_name)
        row    = SignalLog(**fields)

        async with AsyncSessionLocal() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)

        logger.debug(
            f"[signal_logger] logged {fields['strategy']}:{fields['symbol']} "
            f"signal={fields['signal']} id={row.id}"
        )
        return row.id

    except Exception as e:
        logger.error(f"[signal_logger] failed to log signal: {e}")
        return None


async def mark_executed(log_id: str) -> None:
    """
    Call this after execute_trade_fn() succeeds so we can filter
    the training set to only rows where we actually entered the market.
    """
    if not log_id:
        return
    try:
        from sqlalchemy import update
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(SignalLog)
                .where(SignalLog.id == log_id)
                .values(executed=True)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"[signal_logger] mark_executed failed: {e}")


async def mark_failed(log_id: str, reason: str = "execution_failed") -> None:
    """
    Called by base_strategy when execute_trade_fn returns a non-success result.
    Logs the failure reason so the trainer can distinguish:
      - never attempted  (executed=False, fail_reason=None)
      - attempted, failed (executed=False, fail_reason set)
      - filled           (executed=True)
    """
    if not log_id:
        return
    try:
        from sqlalchemy import update
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(SignalLog)
                .where(SignalLog.id == log_id)
                .values(fail_reason=reason)
            )
            await db.commit()
        logger.debug(f"[signal_logger] mark_failed id={log_id} reason={reason}")
    except Exception as e:
        logger.error(f"[signal_logger] mark_failed failed: {e}")