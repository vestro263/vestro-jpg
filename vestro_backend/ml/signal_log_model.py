"""
signal_log_model.py
===================
Two new SQLAlchemy tables:

  SignalLog          — one row per signal computed by any strategy
                       label is written back by outcome_labeler.py
                       after the outcome window elapses

  CalibrationConfig  — one row per symbol, overwritten each time
                       calibration_trainer.py runs
                       signal_engine / strategies read this at runtime

Add to your existing init_db() call:
    from .ml.signal_log_model import SignalLog, CalibrationConfig
    # Base.metadata.create_all will pick them up automatically
    # if they share the same Base as your other models.
    # If not, call SignalLogBase.metadata.create_all(conn) separately.
"""

from sqlalchemy import (
    Column, String, Float, Integer, DateTime, Text,
    Boolean, Index, func,
)
from sqlalchemy.orm import DeclarativeBase
import uuid


def _gen_id():
    return str(uuid.uuid4())


# ── Use your existing Base if you import it here, or keep a
#    separate one and call create_all on both. ─────────────────
class SignalLogBase(DeclarativeBase):
    pass


class SignalLog(SignalLogBase):
    """
    Immutable row written every time a strategy computes a non-HOLD signal
    (and also on the first HOLD, so we have negative examples too).

    Outcome columns are NULL until outcome_labeler.py fills them in.
    """
    __tablename__ = "signal_logs"

    id          = Column(String,  primary_key=True, default=_gen_id)

    # ── Identity ──────────────────────────────────────────────
    strategy    = Column(String,  nullable=False, index=True)   # "V75" | "Crash500"
    symbol      = Column(String,  nullable=False, index=True)   # "R_75" | "CRASH500"
    signal      = Column(String,  nullable=False)               # "BUY" | "SELL" | "HOLD"
    direction   = Column(Integer, nullable=False)               # +1 / -1 / 0

    # ── Entry snapshot ────────────────────────────────────────
    entry_price = Column(Float,   nullable=True)
    sl_price    = Column(Float,   nullable=True)
    tp_price    = Column(Float,   nullable=True)
    amount      = Column(Float,   nullable=True)

    # ── Indicator features (all nullable — Crash500 skips some) ──
    rsi         = Column(Float,   nullable=True)
    adx         = Column(Float,   nullable=True)
    atr         = Column(Float,   nullable=True)
    ema_50      = Column(Float,   nullable=True)
    ema_200     = Column(Float,   nullable=True)
    macd_hist   = Column(Float,   nullable=True)
    tss_score   = Column(Integer, nullable=True)
    checklist   = Column(Integer, nullable=True)   # V75 only
    confidence  = Column(Float,   nullable=True)
    atr_zone    = Column(String,  nullable=True)   # low/normal/elevated/extreme

    # ── Crash500 extra features ───────────────────────────────
    drop_spike  = Column(Float,   nullable=True)
    recovery    = Column(Float,   nullable=True)
    spike_score = Column(Integer, nullable=True)   # 0-4 checklist score

    # ── Execution result ──────────────────────────────────────
    fail_reason = Column(String,  nullable=True)   # set by mark_failed()

    # ── Outcome (triple-barrier) ──────────────────────────────
    # NULL until labeler runs; then +1 (WIN) / -1 (LOSS) / 0 (neutral/timeout)
    label_15m   = Column(Integer, nullable=True)   # primary metric
    label_30m   = Column(Integer, nullable=True)
    label_60m   = Column(Integer, nullable=True)   # secondary confirmation
    label_90m   = Column(Integer, nullable=True)
    label_4h    = Column(Integer, nullable=True)   # extended window
    labeled_at  = Column(DateTime, nullable=True)

    # ── Timestamps ───────────────────────────────────────────
    captured_at = Column(DateTime, server_default=func.now(), nullable=False)
    executed    = Column(Boolean,  default=False)   # did we actually fire a trade?

    __table_args__ = (
        Index("ix_sl_symbol_signal",    "symbol", "signal"),
        Index("ix_sl_captured_at",      "captured_at"),
        Index("ix_sl_unlabeled",        "captured_at", "label_15m"),   # labeler query
    )


class CalibrationConfig(SignalLogBase):
    """
    One row per symbol. Overwritten on every training run.
    Stores the ML-derived thresholds that replace hard-coded values.
    """
    __tablename__ = "calibration_config"

    id          = Column(String,  primary_key=True, default=_gen_id)
    symbol      = Column(String,  nullable=False, unique=True, index=True)
    strategy    = Column(String,  nullable=False)

    # ── Learned thresholds ────────────────────────────────────
    rsi_buy_max     = Column(Float, nullable=True)   # was hard-coded 45
    rsi_sell_min    = Column(Float, nullable=True)   # was hard-coded 55
    adx_min         = Column(Float, nullable=True)   # was hard-coded 25
    tss_min         = Column(Integer, nullable=True) # was hard-coded 3
    checklist_min   = Column(Integer, nullable=True) # was hard-coded 4
    confidence_min  = Column(Float, nullable=True)   # was hard-coded 0 (no gate)
    spike_min       = Column(Float, nullable=True)   # Crash500: was 3.0
    recovery_min    = Column(Float, nullable=True)   # Crash500: was 0.5

    # ── Model quality metadata ────────────────────────────────
    n_samples       = Column(Integer, nullable=True)
    precision       = Column(Float,   nullable=True)   # on hold-out set
    recall          = Column(Float,   nullable=True)
    f1              = Column(Float,   nullable=True)
    feature_importance_json = Column(Text, nullable=True)  # {"rsi": 0.32, ...}

    trained_at  = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_cc_symbol", "symbol"),
    )