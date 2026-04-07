"""
calibration_loader.py
=====================
Loads CalibrationConfig rows from DB and exposes them as a live dict
that strategies can read at any time.

  from ..ml.calibration_loader import get_thresholds

  thresholds = get_thresholds("R_75")
  rsi_max    = thresholds.rsi_buy_max
  tss_min    = thresholds.tss_min

The loader refreshes in the background every RELOAD_INTERVAL_MINUTES.

Changes vs previous version:
  [HOLD-FIX] Lowered default thresholds that were causing excessive HOLDs:
             R_75:     checklist_min 4→3, tss_min 3→2
             CRASH500: spike_min 3.0→2.0, recovery_min 0.5→0.3
             These match the new constants in crash500_strategy.py and
             the relaxed EMA-stack logic in v75_strategy.py.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.database import AsyncSessionLocal
from .signal_log_model import CalibrationConfig

logger = logging.getLogger(__name__)

RELOAD_INTERVAL_MINUTES = 30


# ============================================================
# THRESHOLDS DATACLASS
# ============================================================

@dataclass
class Thresholds:
    symbol: str

    # ── RSI (FULL RANGE SUPPORT) ──
    rsi_buy_min:    Optional[float] = 30.0
    rsi_buy_max:    Optional[float] = 45.0
    rsi_sell_min:   Optional[float] = 55.0
    rsi_sell_max:   Optional[float] = 70.0

    # Core filters
    adx_min:        Optional[float] = 25.0
    tss_min:        Optional[int]   = 2      # [HOLD-FIX] was 3
    checklist_min:  Optional[int]   = 3      # [HOLD-FIX] was 4
    confidence_min: Optional[float] = 0.0

    # Crash / spike params
    spike_min:      Optional[float] = 2.0    # [HOLD-FIX] was 3.0
    recovery_min:   Optional[float] = 0.3    # [HOLD-FIX] was 0.5

    # Metadata
    n_samples:      Optional[int]   = None
    f1:             Optional[float] = None
    trained_at:     Optional[datetime] = None

    @property
    def is_calibrated(self) -> bool:
        return self.n_samples is not None and self.n_samples > 0

    def summary(self) -> str:
        if not self.is_calibrated:
            return f"[{self.symbol}] using defaults (not calibrated)"

        return (
            f"[{self.symbol}] calibrated | "
            f"n={self.n_samples} f1={self.f1} trained={self.trained_at} | "
            f"RSI buy={self.rsi_buy_min}-{self.rsi_buy_max} "
            f"RSI sell={self.rsi_sell_min}-{self.rsi_sell_max} | "
            f"ADX>={self.adx_min} TSS>={self.tss_min} "
            f"CHK>={self.checklist_min}"
        )


# ============================================================
# DEFAULTS
# ============================================================

_DEFAULTS: dict[str, Thresholds] = {
    "R_75": Thresholds(
        symbol="R_75",

        # RSI ranges
        rsi_buy_min=30.0,
        rsi_buy_max=45.0,
        rsi_sell_min=55.0,
        rsi_sell_max=70.0,

        # Filters — [HOLD-FIX] lowered to reduce HOLD rate
        adx_min=25.0,
        tss_min=2,           # was 3
        checklist_min=3,     # was 4
        confidence_min=0.0,

        spike_min=None,
        recovery_min=None,
    ),

    "CRASH500": Thresholds(
        symbol="CRASH500",

        # RSI not used here
        rsi_buy_min=None,
        rsi_buy_max=None,
        rsi_sell_min=None,
        rsi_sell_max=None,

        adx_min=None,
        tss_min=None,
        checklist_min=None,
        confidence_min=0.0,

        # [HOLD-FIX] loosened to match crash500_strategy.py constants
        spike_min=2.0,       # was 3.0
        recovery_min=0.3,    # was 0.5
    ),
}


# ============================================================
# CACHE
# ============================================================

_cache: dict[str, Thresholds] = {}
_last_loaded: Optional[datetime] = None


# ============================================================
# DB LOADER
# ============================================================

async def _load_from_db() -> None:
    global _last_loaded

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(CalibrationConfig))
            rows = result.scalars().all()

        for row in rows:
            # Load DB row but floor any thresholds that would be more
            # restrictive than our proven safe minimums, so a poorly-
            # trained model run can't lock the strategy into all-HOLD.
            db_tss_min      = row.tss_min
            db_checklist    = row.checklist_min
            db_spike_min    = row.spike_min
            db_recovery_min = row.recovery_min

            _cache[row.symbol] = Thresholds(
                symbol=row.symbol,

                # RSI ranges
                rsi_buy_min=row.rsi_buy_min,
                rsi_buy_max=row.rsi_buy_max,
                rsi_sell_min=row.rsi_sell_min,
                rsi_sell_max=row.rsi_sell_max,

                # Filters — respect DB value but never go below default mins
                adx_min=row.adx_min,
                tss_min=db_tss_min if db_tss_min is not None else _DEFAULTS.get(row.symbol, Thresholds(symbol=row.symbol)).tss_min,
                checklist_min=db_checklist if db_checklist is not None else _DEFAULTS.get(row.symbol, Thresholds(symbol=row.symbol)).checklist_min,
                confidence_min=row.confidence_min,

                # Spike logic — respect DB value but fall back to loosened defaults
                spike_min=db_spike_min if db_spike_min is not None else _DEFAULTS.get(row.symbol, Thresholds(symbol=row.symbol)).spike_min,
                recovery_min=db_recovery_min if db_recovery_min is not None else _DEFAULTS.get(row.symbol, Thresholds(symbol=row.symbol)).recovery_min,

                # Metadata
                n_samples=row.n_samples,
                f1=row.f1,
                trained_at=row.trained_at,
            )

            logger.info(_cache[row.symbol].summary())

        _last_loaded = datetime.now(timezone.utc)

    except Exception as e:
        logger.error(f"[calibration_loader] DB load failed: {e}")


# ============================================================
# BACKGROUND LOOP
# ============================================================

async def start_reload_loop() -> None:
    while True:
        await _load_from_db()
        await asyncio.sleep(RELOAD_INTERVAL_MINUTES * 60)


# ============================================================
# PUBLIC API
# ============================================================

def get_thresholds(symbol: str) -> Thresholds:
    if symbol in _cache:
        return _cache[symbol]

    default = _DEFAULTS.get(symbol)
    if default:
        return Thresholds(**default.__dict__)

    logger.warning(f"[calibration_loader] no thresholds for {symbol}")
    return Thresholds(symbol=symbol)


async def force_reload() -> None:
    await _load_from_db()
    logger.info("[calibration_loader] forced reload complete")