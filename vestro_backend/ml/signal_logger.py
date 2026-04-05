"""
signal_logger.py
================
Writes one SignalLog row per signal computed by any strategy.
Fire-and-forget — never raises, so a DB error never kills the pipeline.
"""

import logging
from datetime import datetime, timezone

from ..database import AsyncSessionLocal
from .signal_log_model import SignalLog

logger = logging.getLogger(__name__)


def _extract_features(signal: dict, strategy_name: str) -> dict:
    meta       = signal.get("meta", {})
    # V75 packs computed indicators into "indicators" key.
    # Crash500 doesn't use this key — it falls back to meta.
    indicators = signal.get("indicators", {})

    raw_signal = signal.get("signal", "HOLD")
    if isinstance(raw_signal, dict):
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

        # V75 indicators — populated from indicators key
        rsi         = indicators.get("rsi"),
        adx         = indicators.get("adx"),
        atr         = indicators.get("atr") or meta.get("atr_val"),
        ema_50      = indicators.get("ema_50"),
        ema_200     = indicators.get("ema_200"),
        macd_hist   = indicators.get("macd_hist"),
        tss_score   = meta.get("tss"),
        checklist   = meta.get("checklist"),
        confidence  = signal.get("confidence"),
        atr_zone    = meta.get("atr_zone"),

        # Crash500 extras — populated from meta
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
    """Persist one SignalLog row. Returns new row id, or None on error."""
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
    """Mark a signal row as having resulted in a real trade."""
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
    Distinguishes: never attempted / attempted+failed / filled.
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