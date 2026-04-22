"""
calibration_loader.py
=====================
Loads CalibrationConfig rows from DB and exposes them as a live dict.
Fixed: rsi_buy_min and rsi_sell_max are not DB columns — use defaults only.
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
# Thresholds dataclass
# =============================================================================

@dataclass
class Thresholds:
    symbol: str

    # RSI — rsi_buy_min and rsi_sell_max are NOT in CalibrationConfig DB
    # They are defaults only, never read from DB
    rsi_buy_min:    Optional[float] = 30.0
    rsi_buy_max:    Optional[float] = 45.0
    rsi_sell_min:   Optional[float] = 55.0
    rsi_sell_max:   Optional[float] = 70.0

    adx_min:        Optional[float] = 25.0
    tss_min:        Optional[int]   = 2
    checklist_min:  Optional[int]   = 3
    confidence_min: Optional[float] = 0.0

    spike_min:      Optional[float] = 2.0
    recovery_min:   Optional[float] = 0.3

    current_regime: str = "UNKNOWN"

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
        base = self.checklist_min or 3
        return base + 1 if self.is_range_regime else base

    def summary(self) -> str:
        if not self.is_calibrated:
            return (
                f"[{self.symbol}] using defaults (not calibrated) "
                f"regime={self.current_regime}"
            )
        return (
            f"[{self.symbol}] calibrated | "
            f"n={self.n_samples} f1={self.f1} trained={self.trained_at} | "
            f"RSI buy≤{self.rsi_buy_max} sell≥{self.rsi_sell_min} | "
            f"ADX>={self.adx_min} TSS>={self.tss_min} "
            f"CHK>={self.checklist_min} conf>={self.confidence_min} "
            f"regime={self.current_regime}"
        )


# =============================================================================
# Defaults
# =============================================================================

_DEFAULTS: dict[str, Thresholds] = {
    "R_75": Thresholds(
        symbol         = "R_75",
        rsi_buy_min    = 30.0,
        rsi_buy_max    = 45.0,
        rsi_sell_min   = 55.0,
        rsi_sell_max   = 70.0,
        adx_min        = 25.0,
        tss_min        = 1,
        checklist_min  = 2,
        confidence_min = 0.0,
        spike_min      = None,
        recovery_min   = None,
    ),
    "R_25": Thresholds(
        symbol         = "R_25",
        rsi_buy_min    = 30.0,
        rsi_buy_max    = 50.0,
        rsi_sell_min   = 50.0,
        rsi_sell_max   = 70.0,
        adx_min        = 20.0,
        tss_min        = 2,
        checklist_min  = 3,
        confidence_min = 0.0,
        spike_min      = None,
        recovery_min   = None,
    ),
    "CRASH500": Thresholds(
        symbol         = "CRASH500",
        rsi_buy_min    = None,
        rsi_buy_max    = None,
        rsi_sell_min   = None,
        rsi_sell_max   = None,
        adx_min        = None,
        tss_min        = None,
        checklist_min  = None,
        confidence_min = 0.0,
        spike_min      = 2.0,
        recovery_min   = 0.3,
    ),
}


# =============================================================================
# Cache
# =============================================================================

_cache:        dict[str, Thresholds] = {}
_last_loaded:  Optional[datetime]    = None


# =============================================================================
# DB loader — only reads columns that exist in CalibrationConfig
# =============================================================================

async def _load_from_db() -> None:
    global _last_loaded

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(CalibrationConfig))
            rows   = result.scalars().all()

        for row in rows:
            # Preserve live regime so a DB reload doesn't reset it mid-session
            existing_regime = _cache.get(
                row.symbol, Thresholds(symbol=row.symbol)
            ).current_regime

            default = _DEFAULTS.get(row.symbol, Thresholds(symbol=row.symbol))

            _cache[row.symbol] = Thresholds(
                symbol = row.symbol,

                # rsi_buy_min / rsi_sell_max NOT in DB — always use defaults
                rsi_buy_min  = default.rsi_buy_min,
                rsi_sell_max = default.rsi_sell_max,

                # These ARE in DB
                rsi_buy_max   = row.rsi_buy_max    if row.rsi_buy_max   is not None else default.rsi_buy_max,
                rsi_sell_min  = row.rsi_sell_min   if row.rsi_sell_min  is not None else default.rsi_sell_min,
                adx_min       = row.adx_min        if row.adx_min       is not None else default.adx_min,
                tss_min       = row.tss_min        if row.tss_min       is not None else default.tss_min,
                checklist_min = row.checklist_min  if row.checklist_min is not None else default.checklist_min,
                confidence_min= row.confidence_min if row.confidence_min is not None else default.confidence_min,
                spike_min     = row.spike_min      if row.spike_min     is not None else default.spike_min,
                recovery_min  = row.recovery_min   if row.recovery_min  is not None else default.recovery_min,

                current_regime = existing_regime,
                n_samples      = row.n_samples,
                f1             = row.f1,
                trained_at     = row.trained_at,
            )

            logger.info(_cache[row.symbol].summary())

        _last_loaded = datetime.now(timezone.utc)
        logger.info(
            f"[calibration_loader] loaded {len(rows)} configs from DB"
        )

    except Exception as exc:
        logger.error(f"[calibration_loader] DB load failed: {exc}", exc_info=True)


# =============================================================================
# Background reload loop
# =============================================================================

async def start_reload_loop() -> None:
    while True:
        await _load_from_db()
        await asyncio.sleep(RELOAD_INTERVAL_MINUTES * 60)


# =============================================================================
# Public API
# =============================================================================

def get_thresholds(symbol: str) -> Thresholds:
    if symbol in _cache:
        return _cache[symbol]
    default = _DEFAULTS.get(symbol)
    if default:
        return Thresholds(**default.__dict__)
    logger.warning(f"[calibration_loader] no thresholds for {symbol}")
    return Thresholds(symbol=symbol)


def set_cached_regime(symbol: str, regime_str: str) -> None:
    if symbol in _cache:
        _cache[symbol].current_regime = regime_str
    else:
        t = get_thresholds(symbol)
        t.current_regime = regime_str
        _cache[symbol]   = t
    logger.info(f"[calibration_loader] regime set {symbol} → {regime_str}")


def load_calibrated_model(symbol: str):
    path = _MODEL_DIR / f"{symbol}_calibrated.pkl"
    if not path.exists():
        logger.debug(f"[calibration_loader] no model found for {symbol} at {path}")
        return None
    try:
        model = joblib.load(path)
        logger.info(f"[calibration_loader] loaded calibrated model for {symbol}")
        return model
    except Exception as exc:
        logger.error(f"[calibration_loader] model load failed for {symbol}: {exc}")
        return None


async def force_reload() -> None:
    await _load_from_db()
    logger.info("[calibration_loader] forced reload complete")