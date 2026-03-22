"""
Boom & Crash Strategy — Section 7 of the strategy document.
Detects spike compression setups and manages spike entries.
"""

import logging
import time
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Index metadata ─────────────────────────────────────────────────────────
INDEX_CONFIG = {
    "Boom 1000 Index":  {"type": "boom", "sl_points": 10,  "spike_freq": 1000},
    "Boom 500 Index":   {"type": "boom", "sl_points": 15,  "spike_freq": 500},
    "Crash 1000 Index": {"type": "crash", "sl_points": 10, "spike_freq": 1000},
    "Crash 500 Index":  {"type": "crash", "sl_points": 15, "spike_freq": 500},
}


class BoomCrashAnalyzer:
    """
    Analyzes Boom/Crash indices for spike compression setups.
    Strategy rules from Section 7:
      - Wait for 6-10 consecutive small candles (compression)
      - RSI on 1M must be < 35 (crash buy) or > 65 (boom sell)
      - Volume declining during compression
      - Enter ONLY at candle close after spike
    """

    def __init__(self, symbol: str, config: dict):
        self.symbol  = symbol
        self.cfg     = INDEX_CONFIG.get(symbol, {})
        self.bc_cfg  = config.get("boom_crash", {})
        self.min_compression = self.bc_cfg.get("min_compression_candles", 6)
        self.rsi_buy_max     = self.bc_cfg.get("rsi_1m_buy_max", 35)
        self.rsi_sell_min    = self.bc_cfg.get("rsi_1m_sell_min", 65)
        self._session_spike_count = 0
        self._max_spikes          = self.bc_cfg.get("max_spike_trades_per_session", 2)

    def reset_session(self):
        self._session_spike_count = 0
        logger.info(f"Boom/Crash session reset for {self.symbol}")

    # ── Compression detection ──────────────────────────────────────────────
    def detect_compression(self, df: pd.DataFrame) -> dict:
        """
        Returns compression info dict:
          compressed: bool
          candle_count: int
          avg_body_ratio: float  (body / prior candles avg body)
          volume_declining: bool
        """
        if len(df) < self.min_compression + 5:
            return {"compressed": False}

        closes = df["close"].values
        opens  = df["open"].values
        highs  = df["high"].values
        lows   = df["low"].values
        vols   = df["volume"].values

        # Body sizes for recent candles
        bodies = np.abs(closes - opens)
        prior_avg = np.mean(bodies[-20:-self.min_compression])

        # Check last N candles for small bodies
        recent_bodies = bodies[-self.min_compression:]
        threshold     = prior_avg * 0.5   # bodies < 50% of prior avg

        small_count = np.sum(recent_bodies < threshold)
        compressed  = small_count >= self.min_compression

        # Volume trend during compression
        recent_vol  = vols[-self.min_compression:]
        vol_slope   = np.polyfit(range(len(recent_vol)), recent_vol, 1)[0]
        vol_declining = vol_slope < 0

        return {
            "compressed":       compressed,
            "candle_count":     int(small_count),
            "avg_body_ratio":   float(np.mean(recent_bodies) / prior_avg) if prior_avg > 0 else 1.0,
            "volume_declining": vol_declining,
            "prior_avg_body":   float(prior_avg),
        }

    # ── Spike detection ────────────────────────────────────────────────────
    def detect_spike(self, df: pd.DataFrame) -> dict:
        """
        Detects if the last candle is a spike.
        A spike candle has a body ≥ 5× the prior average body.
        Returns: {is_spike, direction, spike_size}
        """
        if len(df) < 10:
            return {"is_spike": False}

        closes = df["close"].values
        opens  = df["open"].values

        bodies      = np.abs(closes - opens)
        prior_avg   = np.mean(bodies[-20:-1])
        last_body   = bodies[-1]

        if prior_avg == 0:
            return {"is_spike": False}

        spike_ratio = last_body / prior_avg
        is_spike    = spike_ratio >= 5.0

        if not is_spike:
            return {"is_spike": False}

        # Direction: boom spike goes up, crash spike goes down
        direction = 1 if closes[-1] > opens[-1] else -1

        return {
            "is_spike":    True,
            "direction":   direction,
            "spike_size":  float(last_body),
            "spike_ratio": float(spike_ratio),
            "spike_high":  float(df["high"].iloc[-1]),
            "spike_low":   float(df["low"].iloc[-1]),
        }

    # ── RSI check on 1M ───────────────────────────────────────────────────
    def calc_rsi_simple(self, closes: np.ndarray, period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes)
        gains  = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_g  = np.mean(gains[-period:])
        avg_l  = np.mean(losses[-period:])
        if avg_l == 0:
            return 100.0
        rs = avg_g / avg_l
        return float(100.0 - (100.0 / (1.0 + rs)))

    # ── Full setup evaluation ──────────────────────────────────────────────
    def evaluate(self, df_1m: pd.DataFrame, df_5m: pd.DataFrame) -> dict:
        """
        Full Section 7 checklist.
        df_1m: 1-minute bars, df_5m: 5-minute bars.
        Returns signal dict with direction and SL/TP.
        """
        index_type = self.cfg.get("type", "boom")  # "boom" or "crash"
        sl_pts     = self.cfg.get("sl_points", 10)

        result = {
            "symbol":    self.symbol,
            "direction": 0,
            "approved":  False,
            "reason":    "",
            "sl_points": sl_pts,
        }

        # Session spike limit
        if self._session_spike_count >= self._max_spikes:
            result["reason"] = f"Session spike limit reached ({self._max_spikes})"
            return result

        # 1. Compression on 5M
        comp = self.detect_compression(df_5m)
        if not comp["compressed"]:
            result["reason"] = f"No compression ({comp.get('candle_count',0)}/{self.min_compression} small candles)"
            return result

        # 2. Spike detection on 1M (most recent candle)
        spike = self.detect_spike(df_1m)
        if not spike["is_spike"]:
            result["reason"] = "No spike detected on 1M"
            return result

        # 3. Spike direction must match index type
        if index_type == "boom"  and spike["direction"] != 1:
            result["reason"] = "Boom spike must be upward"
            return result
        if index_type == "crash" and spike["direction"] != -1:
            result["reason"] = "Crash spike must be downward"
            return result

        # 4. RSI on 1M
        rsi_1m = self.calc_rsi_simple(df_1m["close"].values)
        if index_type == "crash" and rsi_1m > self.rsi_buy_max:
            result["reason"] = f"Crash RSI {rsi_1m:.1f} > {self.rsi_buy_max} (need oversold)"
            return result
        if index_type == "boom" and rsi_1m < self.rsi_sell_min:
            result["reason"] = f"Boom RSI {rsi_1m:.1f} < {self.rsi_sell_min} (need overbought)"
            return result

        # 5. Price at significant 5M level (within 20% of range)
        price_range = df_5m["high"].max() - df_5m["low"].min()
        last_close  = df_1m["close"].iloc[-1]
        dist_from_low  = last_close - df_5m["low"].min()
        dist_from_high = df_5m["high"].max() - last_close
        near_level = (dist_from_low < price_range * 0.2) or \
                     (dist_from_high < price_range * 0.2)

        # 6. Volume declining = confirmed compression
        if not comp["volume_declining"]:
            logger.debug(f"Volume not declining for {self.symbol} — proceeding anyway")

        # Trade direction: after boom spike → SELL; after crash spike → BUY
        trade_direction = -1 if index_type == "boom" else 1
        entry_price     = last_close
        point           = 0.001  # Deriv synthetic index point size

        if trade_direction == -1:  # SELL after boom spike
            sl  = round(spike["spike_high"] + sl_pts * point, 3)
            tp  = round(entry_price - (sl - entry_price) * 2, 3)
        else:                      # BUY after crash spike
            sl  = round(spike["spike_low"]  - sl_pts * point, 3)
            tp  = round(entry_price + (entry_price - sl) * 2, 3)

        result.update({
            "direction":         trade_direction,
            "approved":          True,
            "entry":             entry_price,
            "sl":                sl,
            "tp":                tp,
            "rsi_1m":            round(rsi_1m, 1),
            "spike_ratio":       round(spike["spike_ratio"], 1),
            "compression_count": comp["candle_count"],
            "near_key_level":    near_level,
            "reason": (
                f"{'SELL' if trade_direction==-1 else 'BUY'} after "
                f"{'boom' if index_type=='boom' else 'crash'} spike | "
                f"RSI={rsi_1m:.1f} | Spike={spike['spike_ratio']:.1f}x | "
                f"Compression={comp['candle_count']} candles"
            ),
        })

        self._session_spike_count += 1
        logger.info(f"Boom/Crash signal: {result['reason']}")
        return result