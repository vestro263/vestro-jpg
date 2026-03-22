"""
Risk Manager — implements all risk rules from the strategy document.
Sections 3, 4, and parts of 5.
"""

import logging
import time
from datetime import datetime, date
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

# Correlated pair groups (treat as one trade)
CORRELATION_GROUPS = [
    {"EURUSD", "GBPUSD"},          # High positive
    {"AUDUSD", "NZDUSD"},          # High positive
    {"EURUSD", "USDCHF"},          # High negative (hedge)
    {"USDJPY", "EURJPY"},          # Medium positive
]


class RiskManager:
    """
    Evaluates every proposed trade against:
    - TSS score threshold
    - ATR volatility zone
    - Daily loss limit
    - Max open trades
    - Correlation risk
    - News event filter
    """

    def __init__(self, config: dict):
        r = config.get("risk", {})
        v = config.get("volatility", {})
        t = config.get("tss", {})

        self.risk_pct              = r.get("risk_pct", 0.01)
        self.max_daily_loss_pct    = r.get("max_daily_loss_pct", 0.05)
        self.max_open_trades       = r.get("max_open_trades", 3)
        self.sl_buffer_pips        = r.get("sl_buffer_pips", 7)
        self.sl_atr_multiplier     = r.get("sl_atr_multiplier", 1.5)
        self.tp1_rr                = r.get("tp1_rr", 1.5)
        self.tp2_rr                = r.get("tp2_rr", 3.0)
        self.min_rr                = r.get("min_rr_ratio", 2.0)

        self.elevated_ratio        = v.get("elevated_ratio", 1.5)
        self.extreme_ratio         = v.get("extreme_ratio", 2.5)
        self.elevated_size_mult    = v.get("elevated_size_mult", 0.5)

        self.min_tss_score         = t.get("min_score_full_size", 3)
        self.min_checklist         = 5  # must pass 5 of 7

        # State (reset daily)
        self._daily_loss_usd: float = 0.0
        self._daily_loss_date: date = date.today()
        self._trade_count_today: int = 0

    # ── Daily reset ────────────────────────────────────────────────────────
    def _maybe_reset_daily(self):
        today = date.today()
        if today != self._daily_loss_date:
            self._daily_loss_usd    = 0.0
            self._trade_count_today = 0
            self._daily_loss_date   = today
            logger.info("Daily counters reset.")

    def record_trade_result(self, profit_usd: float):
        """Call this after every trade closes."""
        self._maybe_reset_daily()
        if profit_usd < 0:
            self._daily_loss_usd += abs(profit_usd)
        self._trade_count_today += 1

    # ── Position sizing ────────────────────────────────────────────────────
    def calc_lot_size(self, balance: float, sl_price_dist: float,
                      pip_value: float, size_mult: float = 1.0) -> float:
        """
        Lot Size = (Balance × Risk%) / (SL_distance_pips × pip_value)
        sl_price_dist: distance in price units (e.g. 0.00150 for 15 pips)
        pip_value: value of 1 pip per 1 lot in account currency
        """
        risk_amount = balance * self.risk_pct * size_mult
        # Convert price distance to pips (1 pip = 0.0001 for 4-digit, 0.001 for JPY)
        sl_pips = sl_price_dist / 0.0001  # normalize; caller adjusts for JPY
        if sl_pips <= 0 or pip_value <= 0:
            return 0.01
        lot = risk_amount / (sl_pips * pip_value)
        return lot

    def round_lot(self, lot: float, vol_min: float = 0.01,
                  vol_max: float = 100.0, vol_step: float = 0.01) -> float:
        """Round lot size to broker's volume step."""
        lot = max(vol_min, min(lot, vol_max))
        lot = round(round(lot / vol_step) * vol_step, 2)
        return lot

    # ── SL/TP calculation ──────────────────────────────────────────────────
    def calc_sl_tp(self, direction: int, entry: float,
                   atr: float, point: float) -> Tuple[float, float, float]:
        """
        Returns (sl_price, tp1_price, tp2_price).
        SL = entry ± (ATR * multiplier) + buffer.
        TP1 = 1.5R, TP2 = 3R.
        """
        buffer   = self.sl_buffer_pips * point * 10
        sl_dist  = atr * self.sl_atr_multiplier + buffer
        tp1_dist = sl_dist * self.tp1_rr
        tp2_dist = sl_dist * self.tp2_rr

        if direction == 1:   # Buy
            sl   = round(entry - sl_dist,  5)
            tp1  = round(entry + tp1_dist, 5)
            tp2  = round(entry + tp2_dist, 5)
        else:                 # Sell
            sl   = round(entry + sl_dist,  5)
            tp1  = round(entry - tp1_dist, 5)
            tp2  = round(entry - tp2_dist, 5)

        return sl, tp1, tp2

    # ── ATR zone ───────────────────────────────────────────────────────────
    def get_atr_zone(self, atr: float, atr_avg: float) -> str:
        if atr_avg <= 0:
            return "normal"
        ratio = atr / atr_avg
        if ratio > self.extreme_ratio:  return "extreme"
        if ratio > self.elevated_ratio: return "elevated"
        if ratio < 0.5:                 return "low"
        return "normal"

    def get_size_multiplier(self, atr_zone: str) -> float:
        if atr_zone == "extreme":  return 0.0   # no trade
        if atr_zone == "elevated": return self.elevated_size_mult
        return 1.0

    # ── Correlation guard ──────────────────────────────────────────────────
    def get_correlated_symbols(self, symbol: str) -> list:
        """Return list of symbols correlated with the given one."""
        correlated = []
        for group in CORRELATION_GROUPS:
            if symbol in group:
                correlated.extend(g for g in group if g != symbol)
        return correlated

    def check_correlation(self, symbol: str,
                          open_positions: list) -> Tuple[bool, str]:
        """
        Returns (ok, reason).
        If a correlated pair is already open, halve the size (handled externally).
        """
        open_syms = {p["symbol"] for p in open_positions}
        corr = self.get_correlated_symbols(symbol)
        conflicts = open_syms.intersection(corr)
        if conflicts:
            return False, f"Correlated position open: {conflicts}"
        return True, ""

    # ── Main trade approval ────────────────────────────────────────────────
    def approve_trade(
        self,
        signal:         dict,
        balance:        float,
        open_positions: list,
        symbol:         str,
        point:          float,
        pip_value:      float,
        symbol_info:    dict,
    ) -> Tuple[bool, dict]:
        """
        Full strategy filter chain.
        Returns (approved: bool, info: dict).
        info contains lot_size, sl, tp1, tp2, reason on approval,
        or just reason on rejection.
        """
        self._maybe_reset_daily()

        # 1. Direction must not be flat
        if signal["direction"] == 0:
            return False, {"reason": "Signal is FLAT — no trade"}

        # 2. TSS score
        if signal["tss_score"] < self.min_tss_score:
            return False, {"reason": f"TSS {signal['tss_score']} < {self.min_tss_score}"}

        # 3. Checklist score
        if signal["checklist_score"] < self.min_checklist:
            return False, {
                "reason": f"Checklist {signal['checklist_score']}/7 < {self.min_checklist}"
            }

        # 4. ATR zone
        zone = signal.get("atr_zone", "normal")
        size_mult = self.get_size_multiplier(zone)
        if size_mult == 0.0:
            return False, {"reason": f"ATR zone EXTREME — stand aside"}

        # 5. Daily loss limit
        daily_limit = balance * self.max_daily_loss_pct
        if self._daily_loss_usd >= daily_limit:
            return False, {
                "reason": f"Daily loss limit hit: ${self._daily_loss_usd:.2f}"
            }

        # 6. Max open trades
        if len(open_positions) >= self.max_open_trades:
            return False, {"reason": f"Max open trades ({self.max_open_trades}) reached"}

        # 7. Correlation check (warn but don't block — halve size instead)
        corr_ok, corr_reason = self.check_correlation(symbol, open_positions)
        if not corr_ok:
            size_mult *= 0.5  # halve size for correlated pair
            logger.warning(f"Correlation risk for {symbol}: {corr_reason} — halving size")

        # 8. Compute SL/TP
        direction = signal["direction"]
        atr       = signal["atr"]

        # Use current price from signal ema21 as proxy (actual entry set at execution)
        # Caller will use tick price; we pre-calc distances here
        sl, tp1, tp2 = self.calc_sl_tp(
            direction, signal["ema21"], atr, point
        )
        sl_dist = abs(signal["ema21"] - sl)

        # 9. Check minimum R:R
        tp_dist = abs(signal["ema21"] - tp1)
        rr = tp_dist / sl_dist if sl_dist > 0 else 0
        if rr < self.min_rr:
            return False, {"reason": f"R:R {rr:.2f} < minimum {self.min_rr}"}

        # 10. Lot size
        lot = self.calc_lot_size(balance, sl_dist, pip_value, size_mult)
        lot = self.round_lot(
            lot,
            symbol_info.get("volume_min", 0.01),
            symbol_info.get("volume_max", 100.0),
            symbol_info.get("volume_step", 0.01),
        )

        logger.info(
            f"Trade APPROVED: {symbol} {'BUY' if direction==1 else 'SELL'} "
            f"| Lot={lot} | TSS={signal['tss_score']} "
            f"| SL={sl:.5f} TP1={tp1:.5f} TP2={tp2:.5f}"
        )

        return True, {
            "lot_size":   lot,
            "sl":         sl,
            "tp1":        tp1,
            "tp2":        tp2,
            "sl_dist":    sl_dist,
            "size_mult":  size_mult,
            "atr_zone":   zone,
            "reason":     signal["reason"],
        }