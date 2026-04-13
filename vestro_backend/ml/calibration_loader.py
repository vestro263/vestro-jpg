"""
calibration_loader.py  (upgraded)
===================================
Loads CalibrationConfig rows from DB and exposes them as a live dict
that strategies can read at any time.

Changes vs original
--------------------
1. load_calibrated_model(symbol)
   New public function.  Loads the versioned calibrated model from
   ml/models/{symbol}_calibrated.pkl (the symlink that retrain.py keeps
   up to date).  Returns the model object or None if not found.
   Strategies can call this to get calibrated predict_proba() at runtime.

2. Thresholds.current_regime field
   Added optional field.  Populated from _current_regimes in signal_engine
   via set_cached_regime().  Strategies read it as:
       t = get_thresholds("R_75")
       if t.current_regime == "CRASH": ...

3. set_cached_regime(symbol, regime_str)
   Called by signal_engine._refresh_regimes() to push the latest regime
   into the thresholds cache so strategies get it through the same
   get_thresholds() call they already make.

4. All original thresholds, defaults, and start_reload_loop() are unchanged.
   Existing strategy code requires zero changes to work with this file.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import joblib
from sqlalchemy import select

from app.database import AsyncSessionLocal
from .signal_log_model import CalibrationConfig

logger = logging.getLogger(__name__)

RELOAD_INTERVAL_MINUTES = 30
_MODEL_DIR = Path(__file__).parent / "models"


# =============================================================================
# Thresholds dataclass  (extended)
# =============================================================================

@dataclass
class Thresholds:
    symbol: str

    # ── RSI ──────────────────────────────────────────────────────────────────
    rsi_buy_min:    Optional[float] = 30.0
    rsi_buy_max:    Optional[float] = 45.0
    rsi_sell_min:   Optional[float] = 55.0
    rsi_sell_max:   Optional[float] = 70.0

    # ── Core filters ──────────────────────────────────────────────────────────
    adx_min:        Optional[float] = 25.0
    tss_min:        Optional[int]   = 2
    checklist_min:  Optional[int]   = 3
    confidence_min: Optional[float] = 0.0

    # ── Crash / spike params ──────────────────────────────────────────────────
    spike_min:      Optional[float] = 2.0
    recovery_min:   Optional[float] = 0.3

    # ── Regime (injected by set_cached_regime, read by strategies) ────────────
    # "TREND" | "RANGE" | "HIGH_VOL" | "CRASH" | "UNKNOWN"
    current_regime: str = "UNKNOWN"

    # ── Metadata ──────────────────────────────────────────────────────────────
    n_samples:  Optional[int]      = None
    f1:         Optional[float]    = None
    trained_at: Optional[datetime] = None

    @property
    def is_calibrated(self) -> bool:
        return self.n_samples is not None and self.n_samples > 0

    @property
    def is_crash_regime(self) -> bool:
        return self.current_regime == "CRASH"

    @property
    def is_high_vol_regime(self) -> bool:
        return self.current_regime in ("CRASH", "HIGH_VOL")

    @property
    def is_range_regime(self) -> bool:
        return self.current_regime == "RANGE"

    def effective_checklist_min(self) -> int:
        """
        Returns checklist_min tightened by +1 in RANGE regime.
        Strategies should use this instead of checklist_min directly.
        """
        base = self.checklist_min or 3
        return base + 1 if self.is_range_regime else base

    def summary(self) -> str:
        if not self.is_calibrated:
            return f"[{self.symbol}] using defaults (not calibrated) regime={self.current_regime}"
        return (
            f"[{self.symbol}] calibrated | "
            f"n={self.n_samples} f1={self.f1} trained={self.trained_at} | "
            f"RSI buy={self.rsi_buy_min}-{self.rsi_buy_max} "
            f"RSI sell={self.rsi_sell_min}-{self.rsi_sell_max} | "
            f"ADX>={self.adx_min} TSS>={self.tss_min} "
            f"CHK>={self.checklist_min} regime={self.current_regime}"
        )


# =============================================================================
# Defaults  (unchanged from original)
# =============================================================================

_DEFAULTS: dict[str, Thresholds] = {
    "R_75": Thresholds(
        symbol        = "R_75",
        rsi_buy_min   = 30.0,
        rsi_buy_max   = 45.0,
        rsi_sell_min  = 55.0,
        rsi_sell_max  = 70.0,
        adx_min       = 25.0,
        tss_min       = 1,
        checklist_min = 2,
        confidence_min= 0.0,
        spike_min     = None,
        recovery_min  = None,
    ),
    "R_25": Thresholds(
        symbol        = "R_25",
        rsi_buy_min   = 30.0,
        rsi_buy_max   = 50.0,
        rsi_sell_min  = 50.0,
        rsi_sell_max  = 70.0,
        adx_min       = 20.0,
        tss_min       = 2,
        checklist_min = 3,
        confidence_min= 0.0,
        spike_min     = None,
        recovery_min  = None,
    ),
    "CRASH500": Thresholds(
        symbol        = "CRASH500",
        rsi_buy_min   = None,
        rsi_buy_max   = None,
        rsi_sell_min  = None,
        rsi_sell_max  = None,
        adx_min       = None,
        tss_min       = None,
        checklist_min = None,
        confidence_min= 0.0,
        spike_min     = 2.0,
        recovery_min  = 0.3,
    ),
}


# =============================================================================
# Cache
# =============================================================================

_cache: dict[str, Thresholds]  = {}
_last_loaded: Optional[datetime] = None


# =============================================================================
# DB loader  (unchanged logic, preserves regime field when refreshing)
# =============================================================================

async def _load_from_db() -> None:
    global _last_loaded

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(CalibrationConfig))
            rows   = result.scalars().all()

        for row in rows:
            # Preserve the regime that was injected by signal_engine so a DB
            # reload doesn't reset it to UNKNOWN mid-session.
            existing_regime = _cache.get(row.symbol, Thresholds(symbol=row.symbol)).current_regime

            _cache[row.symbol] = Thresholds(
                symbol        = row.symbol,
                rsi_buy_min   = row.rsi_buy_min,
                rsi_buy_max   = row.rsi_buy_max,
                rsi_sell_min  = row.rsi_sell_min,
                rsi_sell_max  = row.rsi_sell_max,
                adx_min       = row.adx_min,
                tss_min       = (row.tss_min if row.tss_min is not None
                                 else _DEFAULTS.get(row.symbol, Thresholds(symbol=row.symbol)).tss_min),
                checklist_min = (row.checklist_min if row.checklist_min is not None
                                 else _DEFAULTS.get(row.symbol, Thresholds(symbol=row.symbol)).checklist_min),
                confidence_min= row.confidence_min,
                spike_min     = (row.spike_min if row.spike_min is not None
                                 else _DEFAULTS.get(row.symbol, Thresholds(symbol=row.symbol)).spike_min),
                recovery_min  = (row.recovery_min if row.recovery_min is not None
                                 else _DEFAULTS.get(row.symbol, Thresholds(symbol=row.symbol)).recovery_min),
                current_regime= existing_regime,   # ← preserve live regime
                n_samples     = row.n_samples,
                f1            = row.f1,
                trained_at    = row.trained_at,
            )

            logger.info(_cache[row.symbol].summary())

        _last_loaded = datetime.now(timezone.utc)

    except Exception as exc:
        logger.error(f"[calibration_loader] DB load failed: {exc}")


# =============================================================================
# Background reload loop  (unchanged)
# =============================================================================

async def start_reload_loop() -> None:
    while True:
        await _load_from_db()
        await asyncio.sleep(RELOAD_INTERVAL_MINUTES * 60)


# =============================================================================
# Public API
# =============================================================================

def get_thresholds(symbol: str) -> Thresholds:
    """Return live calibrated thresholds, falling back to defaults."""
    if symbol in _cache:
        return _cache[symbol]

    default = _DEFAULTS.get(symbol)
    if default:
        # Return a copy so callers can't mutate the default
        return Thresholds(**default.__dict__)

    logger.warning(f"[calibration_loader] no thresholds for {symbol}")
    return Thresholds(symbol=symbol)


def set_cached_regime(symbol: str, regime_str: str) -> None:
    """
    Called by signal_engine._refresh_regimes() to push the latest regime
    into the live thresholds cache.

    Strategies read it via:
        t = get_thresholds(self.SYMBOL)
        if t.is_crash_regime:
            return hold_signal(...)
    """
    if symbol in _cache:
        _cache[symbol].current_regime = regime_str
    else:
        # Build from defaults and set regime
        t = get_thresholds(symbol)
        t.current_regime = regime_str
        _cache[symbol]   = t

    logger.info(f"[calibration_loader] regime set {symbol} → {regime_str}")


def load_calibrated_model(symbol: str):
    """
    Load the versioned calibrated sklearn model from disk.
    Returns the model object (CalibratedClassifierCV wrapping GBM),
    or None if no model has been trained yet.

    Usage in strategy (optional — for direct probability inference):
        from ml.calibration_loader import load_calibrated_model
        model = load_calibrated_model("R_75")
        if model:
            proba = model.predict_proba([feature_vector])[0]
    """
    symlink = _MODEL_DIR / f"{symbol}_calibrated.pkl"
    if not symlink.exists():
        logger.debug(f"[calibration_loader] no model found for {symbol} at {symlink}")
        return None
    try:
        model = joblib.load(symlink)
        logger.info(f"[calibration_loader] loaded calibrated model for {symbol}")
        return model
    except Exception as exc:
        logger.error(f"[calibration_loader] model load failed for {symbol}: {exc}")
        return None


async def force_reload() -> None:
    """Force an immediate DB reload.  Unchanged interface."""
    await _load_from_db()
    logger.info("[calibration_loader] forced reload complete")