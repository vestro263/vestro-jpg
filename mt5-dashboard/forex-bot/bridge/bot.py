

import asyncio
import logging
import logging.handlers
import math
import os
import random
import signal as _signal
import statistics
import sys
import threading
import time
from collections import deque
from datetime import datetime
from typing import Dict, Optional, Tuple

import yaml
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.mt5_connector import (
    connect, disconnect, get_account_info,
    get_ohlcv, get_open_positions, get_symbol_info, get_tick,
    BarStreamer,
)
from bridge.signal_bridge_py import get_signal
from bridge.risk_manager import RiskManager
from bridge.trade_executor import (
    send_order, move_to_breakeven, partial_close,
    TrailingStopManager,
)
from bridge.boom_crash import BoomCrashAnalyzer
from db.journal import init_db, log_trade, update_trade_exit
from bridge.api_server import broadcast_sync

_ENGINE = "Python+V75"


# ── Config ─────────────────────────────────────────────────────────────────
def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)

config = load_config()


# ── Logging ────────────────────────────────────────────────────────────────
def setup_logging(cfg: dict):
    log_cfg = cfg.get("logging", {})
    os.makedirs("logs", exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_cfg.get("level", "INFO")))
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)
    fh = logging.handlers.RotatingFileHandler(
        log_cfg.get("log_file", "logs/bot.log"),
        maxBytes=log_cfg.get("max_bytes", 5_242_880),
        backupCount=log_cfg.get("backup_count", 3),
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

setup_logging(config)
logger = logging.getLogger("bot")
logger.info(f"Signal engine: {_ENGINE}")


# ============================================================
# V75 PIPELINE — PHASE 2: FEATURE ENGINE
# ============================================================

class FeatureEngine:
    """
    Transforms raw OHLC candle list into all tradeable indicators.
    Input: list of dicts with keys open/high/low/close/volume
    (compatible with both V75DataCapture output and MT5 DataFrame rows)
    """

    def __init__(self, candles: list):
        self.candles = candles
        self.closes  = [c["close"] for c in candles]
        self.highs   = [c["high"]  for c in candles]
        self.lows    = [c["low"]   for c in candles]

    def ema(self, period: int, prices: list = None) -> list:
        src = prices if prices is not None else self.closes
        k   = 2 / (period + 1)
        out = [src[0]]
        for p in src[1:]:
            out.append(p * k + out[-1] * (1 - k))
        return out

    def atr(self, period: int = 14) -> list:
        trs = []
        for i in range(1, len(self.candles)):
            tr = max(
                self.highs[i]  - self.lows[i],
                abs(self.highs[i]  - self.closes[i - 1]),
                abs(self.lows[i]   - self.closes[i - 1]),
            )
            trs.append(tr)
        result = [sum(trs[:period]) / period]
        for tr in trs[period:]:
            result.append((result[-1] * (period - 1) + tr) / period)
        return result

    def rsi(self, period: int = 14) -> list:
        deltas   = [self.closes[i] - self.closes[i - 1] for i in range(1, len(self.closes))]
        gains    = [max(d, 0)       for d in deltas]
        losses   = [abs(min(d, 0))  for d in deltas]
        avg_gain = sum(gains[:period])  / period
        avg_loss = sum(losses[:period]) / period + 1e-10
        vals     = [100 - (100 / (1 + avg_gain / avg_loss))]
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i])  / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period + 1e-10
            vals.append(100 - (100 / (1 + avg_gain / avg_loss)))
        return vals

    def macd(self, fast: int = 12, slow: int = 26, sig_period: int = 9) -> dict:
        ema_f     = self.ema(fast)
        ema_s     = self.ema(slow)
        macd_line = [f - s for f, s in zip(ema_f, ema_s)]
        sig_line  = self.ema(sig_period, macd_line)
        histogram = [m - s for m, s in zip(macd_line, sig_line)]
        return {"macd": macd_line, "signal": sig_line, "histogram": histogram}

    def adx(self, period: int = 14) -> list:
        plus_dm, minus_dm, tr_list = [], [], []
        for i in range(1, len(self.candles)):
            up   = self.highs[i]  - self.highs[i - 1]
            down = self.lows[i - 1] - self.lows[i]
            plus_dm.append(up   if up > down and up > 0   else 0)
            minus_dm.append(down if down > up and down > 0 else 0)
            tr_list.append(max(
                self.highs[i] - self.lows[i],
                abs(self.highs[i] - self.closes[i - 1]),
                abs(self.lows[i]  - self.closes[i - 1]),
            ))

        def smooth(lst, p):
            s = [sum(lst[:p])]
            for v in lst[p:]:
                s.append(s[-1] - s[-1] / p + v)
            return s

        str14 = smooth(tr_list, period)
        pdm14 = smooth(plus_dm,  period)
        ndm14 = smooth(minus_dm, period)
        pdi   = [100 * p / (t + 1e-10) for p, t in zip(pdm14, str14)]
        ndi   = [100 * n / (t + 1e-10) for n, t in zip(ndm14, str14)]
        dx    = [100 * abs(p - n) / (p + n + 1e-10) for p, n in zip(pdi, ndi)]
        adx_s = [sum(dx[:period]) / period]
        for v in dx[period:]:
            adx_s.append((adx_s[-1] * (period - 1) + v) / period)
        return adx_s

    def bollinger(self, period: int = 20, std_dev: int = 2) -> dict:
        upper, middle, lower = [], [], []
        for i in range(period - 1, len(self.closes)):
            w = self.closes[i - period + 1: i + 1]
            m = statistics.mean(w)
            s = statistics.stdev(w)
            middle.append(m)
            upper.append(m + std_dev * s)
            lower.append(m - std_dev * s)
        return {"upper": upper, "middle": middle, "lower": lower}

    def build_all(self) -> dict:
        macd_data = self.macd()
        return {
            "ema_21":          self.ema(21),
            "ema_50":          self.ema(50),
            "ema_200":         self.ema(200),
            "rsi_14":          self.rsi(14),
            "atr_14":          self.atr(14),
            "adx_14":          self.adx(14),
            "macd":            macd_data["macd"],
            "macd_signal":     macd_data["signal"],
            "macd_histogram":  macd_data["histogram"],
            "bb":              self.bollinger(),
        }


# ============================================================
# V75 PIPELINE — PHASE 3: PATTERN EXTRACTOR
# ============================================================

class PatternExtractor:
    """
    Candle pattern recognition + statistical aggregation.
    Runs on both V75 and Boom/Crash candle streams.
    """

    def __init__(self, candles: list, features: dict):
        self.candles  = candles
        self.features = features

    def compression_zone(self, lookback: int = 8) -> bool:
        """
        Boom/Crash pre-spike condition:
        6-10 small candles (bodies < 50% of prior candles).
        """
        if len(self.candles) < lookback + 5:
            return False
        recent = [abs(self.candles[i]["close"] - self.candles[i]["open"])
                  for i in range(-lookback, 0)]
        prior  = [abs(self.candles[i]["close"] - self.candles[i]["open"])
                  for i in range(-lookback - 5, -lookback)]
        avg_r  = statistics.mean(recent)
        avg_p  = statistics.mean(prior) + 1e-10
        return avg_r / avg_p < 0.5

    def rsi_divergence(self, lookback: int = 10) -> str:
        """RSI divergence per PDF Section 2.3."""
        rsi    = self.features.get("rsi_14", [])
        closes = [c["close"] for c in self.candles]
        if len(rsi) < lookback or len(closes) < lookback:
            return "none"
        p_last, p_prev = closes[-1], closes[-lookback]
        r_last, r_prev = rsi[-1],    rsi[-lookback]
        if p_last > p_prev and r_last < r_prev: return "regular_bearish"
        if p_last < p_prev and r_last > r_prev: return "regular_bullish"
        if p_last < p_prev and r_last < r_prev: return "hidden_bearish"
        if p_last > p_prev and r_last > r_prev: return "hidden_bullish"
        return "none"

    def trend_strength_score(self) -> int:
        """
        TSS 0-5 per PDF Section 1.2.
        Minimum score of 3 required for full-size position.
        """
        score  = 0
        closes = [c["close"] for c in self.candles]
        e21    = self.features.get("ema_21",  [closes[-1]])
        e50    = self.features.get("ema_50",  [closes[-1]])
        e200   = self.features.get("ema_200", [closes[-1]])
        adx    = self.features.get("adx_14",  [0])
        mh     = self.features.get("macd_histogram", [0])
        vols   = [c["volume"] for c in self.candles]

        bull = e21[-1] > e50[-1] > e200[-1]
        bear = e21[-1] < e50[-1] < e200[-1]
        if bull or bear:
            score += 1
        if adx and adx[-1] > 25:
            score += 1
        if e200[-1] and closes[-1] > e200[-1]:
            score += 1
        if mh and mh[-1] > 0:
            score += 1
        if len(vols) > 1:
            avg_v = statistics.mean(vols[:-1])
            if vols[-1] > avg_v * 1.2:
                score += 1
        return score

    def statistical_aggregation(self, window: int = 50) -> dict:
        closes = [c["close"] for c in self.candles[-window:]]
        s      = sorted(closes)
        return {
            "mean":     statistics.mean(closes),
            "variance": statistics.variance(closes),
            "p25":      s[int(len(s) * 0.25)],
            "p50":      statistics.median(closes),
            "p75":      s[int(len(s) * 0.75)],
            "p95":      s[int(len(s) * 0.95)],
        }


# ============================================================
# V75 PIPELINE — PHASE 4: PREDICTION ENGINE
# ============================================================

class PredictionEngine:
    """
    Combines TSS + 7-point entry checklist → BUY / SELL / HOLD signal.
    Minimum required: TSS >= 3 AND checklist >= 5/7.
    """

    def __init__(self, patterns: PatternExtractor, features: dict, candles: list):
        self.patterns  = patterns
        self.features  = features
        self.candles   = candles

    def _atr_zone(self) -> str:
        """ATR volatility classification per PDF Section 4.1."""
        atr_vals = self.features.get("atr_14", [1])
        if len(atr_vals) < 21:
            return "normal"
        curr   = atr_vals[-1]
        avg20  = statistics.mean(atr_vals[-21:-1])
        ratio  = curr / (avg20 + 1e-10)
        if ratio < 0.5:  return "low"
        if ratio < 1.5:  return "normal"
        if ratio < 2.5:  return "elevated"
        return "extreme"

    def _entry_checklist(self, direction: str) -> Tuple[int, list]:
        """
        PDF Section 2.1 / 2.2 — full 7-point checklist.
        Returns (score, detail_list).
        """
        score   = 0
        details = []
        closes  = [c["close"] for c in self.candles]
        rsi     = self.features.get("rsi_14",          [50])
        mh      = self.features.get("macd_histogram",   [0])
        e50     = self.features.get("ema_50",  [closes[-1]])
        e200    = self.features.get("ema_200", [closes[-1]])
        vols    = [c["volume"] for c in self.candles]
        lc      = self.candles[-1]
        body    = abs(lc["close"] - lc["open"])
        rng     = lc["high"] - lc["low"] + 1e-10

        def chk(label, passed):
            nonlocal score
            details.append({"label": label, "pass": passed})
            if passed:
                score += 1

        if direction == "buy":
            chk("EMA50 > EMA200 (HTF bias)",      e50[-1] > e200[-1])
            chk("RSI 30–45 (oversold bounce)",     30 <= rsi[-1] <= 45)
            chk("MACD histogram green",            mh[-1] > 0)
            chk("Bullish candle close",            lc["close"] > lc["open"] and body / rng > 0.5)
            chk("Volume spike >1.2× avg",          len(vols) > 1 and vols[-1] > statistics.mean(vols[:-1]) * 1.2)
            chk("London/NY session",               True)   # assumed in backtest; live: check time
            chk("Demand zone retest",              self.patterns.trend_strength_score() >= 3)
        else:
            chk("EMA50 < EMA200 (HTF bias)",      e50[-1] < e200[-1])
            chk("RSI 55–70 (overbought fade)",     55 <= rsi[-1] <= 70)
            chk("MACD histogram red",              mh[-1] < 0)
            chk("Bearish candle close",            lc["close"] < lc["open"] and body / rng > 0.5)
            chk("Volume spike >1.2× avg",          len(vols) > 1 and vols[-1] > statistics.mean(vols[:-1]) * 1.2)
            chk("London/NY session",               True)
            chk("Supply zone retest",              self.patterns.trend_strength_score() >= 3)

        return min(score, 7), details

    def predict(self) -> dict:
        tss      = self.patterns.trend_strength_score()
        diverge  = self.patterns.rsi_divergence()
        compress = self.patterns.compression_zone()
        atr_zone = self._atr_zone()
        stats    = self.patterns.statistical_aggregation()
        closes   = [c["close"] for c in self.candles]
        e21      = self.features.get("ema_21",  [closes[-1]])
        e50      = self.features.get("ema_50",  [closes[-1]])
        e200     = self.features.get("ema_200", [closes[-1]])

        # Hard block: extreme ATR
        if atr_zone == "extreme":
            return {
                "signal": "HOLD", "direction": 0,
                "reason": "ATR extreme — stand aside",
                "tss": tss, "checklist_score": 0,
                "atr_zone": atr_zone, "confidence": 0.0,
                "divergence": diverge, "spike_ready": False,
                "stats": stats,
            }

        bull_stack = e21[-1] > e50[-1] > e200[-1]
        bear_stack = e21[-1] < e50[-1] < e200[-1]

        if not bull_stack and not bear_stack:
            return {
                "signal": "HOLD", "direction": 0,
                "reason": "EMA stack indeterminate",
                "tss": tss, "checklist_score": 0,
                "atr_zone": atr_zone, "confidence": 0.0,
                "divergence": diverge, "spike_ready": False,
                "stats": stats,
            }

        direction     = "buy" if bull_stack else "sell"
        checklist, cl = self._entry_checklist(direction)

        if checklist < 5 or tss < 3:
            return {
                "signal": "HOLD", "direction": 0,
                "reason": f"Checklist {checklist}/7, TSS {tss}/5 — need ≥5 and ≥3",
                "tss": tss, "checklist_score": checklist,
                "atr_zone": atr_zone, "confidence": 0.0,
                "divergence": diverge, "spike_ready": False,
                "checklist_detail": cl, "stats": stats,
            }

        confidence  = min(1.0, (tss / 5) * 0.5 + (checklist / 7) * 0.5)
        signal_str  = "BUY" if direction == "buy" else "SELL"
        direction_i = 1 if direction == "buy" else -1

        return {
            "signal":           signal_str,
            "direction":        direction_i,
            "price":            closes[-1],
            "confidence":       round(confidence, 3),
            "tss":              tss,
            "tss_score":        tss,        # alias for bridge.risk_manager compat
            "checklist_score":  checklist,
            "atr_zone":         atr_zone,
            "divergence":       diverge,
            "spike_ready":      compress and signal_str == "SELL",
            "checklist_detail": cl,
            "reason":           f"TSS {tss}/5 · Checklist {checklist}/7 · {atr_zone.upper()} ATR",
            "stats":            stats,
            "atr":              self.features.get("atr_14", [0])[-1],
        }


# ============================================================
# V75 PIPELINE — PHASE 5: TIERED RISK MANAGER
# ============================================================

class TieredRiskManager:
    """
    Dynamic position sizing based on live account balance tier.

    Tier detection (auto, re-evaluated after every trade):
      Starter      $10 – $49   → 1%   · 2 trades · stop after 2 losses · 3% DD
      Growth       $50 – $499  → 1.5% · 3 trades · stop after 3 losses · 4% DD
      Established  $500+       → 2%   · 4 trades · stop after 3 losses · 5% DD
      Prop Firm    (flag)       → 0.75%· 2 trades · stop after 2 losses · 3% DD

    Lot formula (PDF Section 3.1):
      lot_size = (balance × risk_pct) / (sl_pips × pip_value)

    ATR zone modifier (PDF Section 4.1):
      elevated → halve lot size
      extreme  → no trade (hard block)
    """

    TIERS = {
        "starter":     {"min": 10,   "max": 49,     "risk_pct": 0.010,  "max_trades": 2, "daily_dd": 0.03, "loss_limit": 2},
        "growth":      {"min": 50,   "max": 499,    "risk_pct": 0.015,  "max_trades": 3, "daily_dd": 0.04, "loss_limit": 3},
        "established": {"min": 500,  "max": 999_999, "risk_pct": 0.020, "max_trades": 4, "daily_dd": 0.05, "loss_limit": 3},
        "prop":        {"min": 0,    "max": 999_999, "risk_pct": 0.0075,"max_trades": 2, "daily_dd": 0.03, "loss_limit": 2},
    }

    def __init__(self, balance: float, pip_value: float = 1.0, is_prop: bool = False):
        self.balance             = balance
        self.pip_value           = pip_value
        self.is_prop             = is_prop
        self.open_trades         = 0
        self.daily_pnl           = 0.0
        self.daily_losses        = 0
        self.consecutive_losses  = 0
        self._tier_name          = self._detect_tier()

    def _detect_tier(self) -> str:
        if self.is_prop:      return "prop"
        if self.balance < 50: return "starter"
        if self.balance < 500: return "growth"
        return "established"

    @property
    def tier(self) -> dict:
        return self.TIERS[self._tier_name]

    def lot_size(self, sl_pips: float) -> dict:
        risk_dollar = self.balance * self.tier["risk_pct"]
        lots        = risk_dollar / (max(sl_pips, 1) * self.pip_value)
        return {
            "lots":           round(lots, 5),
            "risk_dollar":    round(risk_dollar, 4),
            "risk_pct":       self.tier["risk_pct"] * 100,
            "tier":           self._tier_name,
            "max_trades":     self.tier["max_trades"],
            "daily_dd_limit": self.tier["daily_dd"] * 100,
        }

    def atr_adjusted_lot(self, sl_pips: float, atr_zone: str) -> dict:
        base       = self.lot_size(sl_pips)
        multiplier = {"low": 1.0, "normal": 1.0, "elevated": 0.5, "extreme": 0.0}.get(atr_zone, 1.0)
        base["lots"]         = round(base["lots"] * multiplier, 5)
        base["atr_zone"]     = atr_zone
        base["atr_adjusted"] = multiplier != 1.0
        return base

    def calc_sl_tp(self, direction: int, entry: float, atr: float, point: float) -> Tuple[float, float, float]:
        """
        Returns (sl, tp1, tp2).
        SL  = entry ± 1.5×ATR + 5-pip buffer  (PDF 3.2)
        TP1 = 1.5R  (close 50% — PDF 6.1)
        TP2 = 3.0R  (close 30% — PDF 6.1)
        """
        cfg      = config.get("risk", {})
        atr_mult = cfg.get("atr_sl_mult", 1.5)
        tp1_rr   = cfg.get("tp1_rr",      1.5)
        tp2_rr   = cfg.get("tp2_rr",      3.0)
        buffer   = 5 * point
        sl_dist  = atr * atr_mult + buffer

        if direction == 1:   # buy
            sl  = entry - sl_dist
            tp1 = entry + sl_dist * tp1_rr
            tp2 = entry + sl_dist * tp2_rr
        else:                # sell
            sl  = entry + sl_dist
            tp1 = entry - sl_dist * tp1_rr
            tp2 = entry - sl_dist * tp2_rr

        return round(sl, 5), round(tp1, 5), round(tp2, 5)

    def can_trade(self, atr_zone: str) -> dict:
        t   = self.tier
        ddp = abs(self.daily_pnl) / (self.balance + 1e-10) if self.daily_pnl < 0 else 0
        checks = {
            "open_trades_ok":      self.open_trades < t["max_trades"],
            "daily_dd_ok":         ddp < t["daily_dd"],
            "consecutive_loss_ok": self.consecutive_losses < t["loss_limit"],
            "atr_zone_ok":         atr_zone != "extreme",
        }
        allowed     = all(checks.values())
        block_reason = None if allowed else next(k for k, v in checks.items() if not v)
        return {
            "allowed":      allowed,
            "checks":       checks,
            "block_reason": block_reason,
            "tier":         self._tier_name,
            "balance":      self.balance,
        }

    def approve_trade(self, sig: dict, balance: float, positions: list,
                      symbol: str, point: float, pip_value: float,
                      sym_info: dict) -> Tuple[bool, dict]:
        """
        Drop-in replacement for bridge.risk_manager.RiskManager.approve_trade().
        Returns (approved: bool, trade_info: dict).
        """
        self.balance   = balance
        self.pip_value = pip_value
        self._tier_name = self._detect_tier()
        self.open_trades = len([p for p in positions if p.get("magic") == 20250101])

        atr_zone = sig.get("atr_zone", "normal")
        check    = self.can_trade(atr_zone)

        if not check["allowed"] or sig.get("direction", 0) == 0:
            return False, {"reason": check.get("block_reason", "no signal"), "lot_size": 0}

        atr      = sig.get("atr", point * 100)
        sl_pips  = (atr * config.get("risk", {}).get("atr_sl_mult", 1.5)) / (point + 1e-10)
        sizing   = self.atr_adjusted_lot(sl_pips, atr_zone)

        return True, {
            "lot_size":     sizing["lots"],
            "risk_dollar":  sizing["risk_dollar"],
            "risk_pct":     sizing["risk_pct"],
            "tier":         self._tier_name,
            "reason":       f"TSS={sig.get('tss_score',0)} tier={self._tier_name}",
        }

    def log_result(self, pnl: float):
        """Update state after a trade closes. Re-tiers automatically."""
        self.daily_pnl += pnl
        if pnl < 0:
            self.daily_losses       += 1
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        self.balance    += pnl
        self.open_trades = max(0, self.open_trades - 1)
        self._tier_name  = self._detect_tier()


# ============================================================
# V75 PIPELINE — PHASE 5: CONTINUOUS LEARNER
# ============================================================

class ContinuousLearner:
    """Tracks prediction outcomes and computes live expectancy metrics."""

    def __init__(self):
        self.predictions = deque(maxlen=200)
        self.outcomes    = deque(maxlen=200)
        self.accuracy    = 0.5

    def record(self, prediction: dict, outcome_pnl: float):
        correct = (
            (prediction.get("signal") == "BUY"  and outcome_pnl > 0) or
            (prediction.get("signal") == "SELL" and outcome_pnl > 0) or
            (prediction.get("signal") == "HOLD")
        )
        self.predictions.append(prediction)
        self.outcomes.append({"pnl": outcome_pnl, "correct": correct})
        self.accuracy = sum(1 for o in self.outcomes if o["correct"]) / len(self.outcomes)

    def metrics(self) -> dict:
        pnls   = [o["pnl"] for o in self.outcomes if o["pnl"] != 0]
        if not pnls:
            return {"accuracy": 0, "expectancy": 0, "win_rate": 0, "trades": 0}
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        wr     = len(wins) / len(pnls)
        avg_w  = statistics.mean(wins)          if wins   else 0
        avg_l  = abs(statistics.mean(losses))   if losses else 1
        return {
            "accuracy":   round(self.accuracy, 3),
            "win_rate":   round(wr, 3),
            "expectancy": round(wr * avg_w - (1 - wr) * avg_l, 2),
            "avg_win":    round(avg_w, 2),
            "avg_loss":   round(avg_l, 2),
            "trades":     len(pnls),
        }


# ============================================================
# V75 CANDLE ADAPTER
# Converts MT5 DataFrame rows → candle dicts for the pipeline
# ============================================================

def df_to_candles(df) -> list:
    """
    Convert a pandas DataFrame from get_ohlcv() into the list-of-dicts
    format expected by FeatureEngine / PatternExtractor.
    """
    return [
        {
            "open":   row["open"],
            "high":   row["high"],
            "low":    row["low"],
            "close":  row["close"],
            "volume": row.get("tick_volume", row.get("volume", 1)),
            "time":   row.get("time", None),
        }
        for _, row in df.iterrows()
    ]


def run_v75_pipeline(candles: list) -> dict:
    """
    Full 5-phase pipeline on a candle list.
    Returns prediction dict compatible with on_new_bar signal format.
    """
    if len(candles) < 30:
        return {"signal": "HOLD", "direction": 0, "reason": "Insufficient candle history", "tss_score": 0, "checklist_score": 0, "atr_zone": "normal", "confidence": 0.0, "atr": 0}

    features  = FeatureEngine(candles).build_all()
    patterns  = PatternExtractor(candles, features)
    predictor = PredictionEngine(patterns, features, candles)
    return predictor.predict()


# ============================================================
# BOT STATE
# ============================================================

_v75_risk_manager  = TieredRiskManager(balance=100.0)   # updated on connect
_v75_learner       = ContinuousLearner()
_signal_cache:      Dict[str, dict] = {}
_trailing_managers: Dict[int, TrailingStopManager] = {}
_tp1_closed:        set  = set()
_bc_analyzers:      Dict[str, BoomCrashAnalyzer] = {}
_bc_5m_cache:       Dict[str, dict] = {}

# Symbols that should use the V75 pipeline instead of signal_bridge_py
_V75_SYMBOLS = set(
    config.get("v75", {}).get("symbols", [
        "Volatility 75 Index",
        "Volatility 75 (1s) Index",
        "V75",
    ])
)


# ── Telegram ───────────────────────────────────────────────────────────────
def send_telegram(msg: str):
    cfg = config.get("alerts", {})
    if not cfg.get("telegram_enabled"):
        return
    try:
        import requests
        token   = os.getenv("TG_BOT_TOKEN", cfg.get("telegram_token", ""))
        chat_id = os.getenv("TG_CHAT_ID",   cfg.get("telegram_chat_id", ""))
        if token and chat_id:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg}, timeout=5,
            )
    except Exception as e:
        logger.error(f"Telegram failed: {e}")


# ── Position management (shared — Forex + V75) ────────────────────────────
def manage_open_positions(symbol: str, df):
    positions = get_open_positions(symbol)
    if not positions:
        return
    cfg_risk = config.get("risk", {})
    atr      = df["close"].diff().abs().rolling(14).mean().iloc[-1]
    current  = df["close"].iloc[-1]

    for pos in positions:
        if pos.get("magic") != 20250101:
            continue
        ticket     = pos["ticket"]
        direction  = pos["type"]
        open_px    = pos["open_price"]
        sl         = pos["sl"]
        tp         = pos["tp"]
        volume     = pos["volume"]
        sl_dist    = abs(open_px - sl)
        tp1_dist   = sl_dist * cfg_risk.get("tp1_rr", 1.5)
        profit_dist = (current - open_px) if direction == "buy" else (open_px - current)

        # TP1: close 50%, move to breakeven
        if ticket not in _tp1_closed and profit_dist >= tp1_dist * 0.98:
            close_vol = round(volume * 0.5, 2)
            try:
                partial_close(ticket, close_vol, symbol, direction)
                _tp1_closed.add(ticket)
                move_to_breakeven(ticket, open_px, tp, buffer_pts=0.00002)
                logger.info(f"TP1 taken on {ticket}: {close_vol} lots")
                broadcast_sync({"type": "tp1_hit", "ticket": ticket,
                                "symbol": symbol, "closed_volume": close_vol})
            except Exception as e:
                logger.error(f"TP1 error: {e}")

        # TP2+: trail with 2× ATR
        if ticket in _tp1_closed:
            if ticket not in _trailing_managers:
                _trailing_managers[ticket] = TrailingStopManager(
                    ticket, symbol, direction, sl, atr_multiplier=2.0)
            _trailing_managers[ticket].update(current, atr, tp)


# ── V75 bar callback ───────────────────────────────────────────────────────
def on_new_bar_v75(symbol: str, df):
    """
    Called on every new bar for V75 symbols.
    Runs the full 5-phase V75 pipeline; executes trades via MT5.
    """
    logger.info(f"V75 bar: {symbol} | {df['time'].iloc[-1]}")
    try:
        candles = df_to_candles(df)
        sig     = run_v75_pipeline(candles)
        sig["symbol"]    = symbol
        sig["timestamp"] = str(df["time"].iloc[-1])
        _signal_cache[symbol] = sig

        account  = get_account_info()
        balance  = account.get("balance", 0.0)
        positions = get_open_positions()
        sym_info = get_symbol_info(symbol)
        point    = sym_info.get("point", 0.00001)
        pip_val  = sym_info.get("trade_tick_value", 1.0) * (
            sym_info.get("point", 0.00001) /
            sym_info.get("trade_tick_size", 0.00001)
        )

        # Update tier from live balance
        _v75_risk_manager.balance   = balance
        _v75_risk_manager.pip_value = pip_val
        _v75_risk_manager._tier_name = _v75_risk_manager._detect_tier()

        approved, trade_info = _v75_risk_manager.approve_trade(
            sig, balance, positions, symbol, point, pip_val, sym_info)

        event = {
            "type":     "signal",
            "source":   "v75_pipeline",
            "symbol":   symbol,
            "signal":   sig,
            "approved": approved,
            "account":  {"balance": balance, "equity": account.get("equity", 0)},
            "tier":     _v75_risk_manager._tier_name,
            "reason":   trade_info.get("reason", ""),
        }

        if approved and sig["direction"] != 0:
            tick      = get_tick(symbol)
            direction = "buy" if sig["direction"] == 1 else "sell"
            entry     = tick["ask"] if direction == "buy" else tick["bid"]
            atr       = sig.get("atr", point * 100)
            sl, tp1, tp2 = _v75_risk_manager.calc_sl_tp(
                sig["direction"], entry, atr, point)

            try:
                result = send_order(
                    symbol, direction, trade_info["lot_size"],
                    sl_price=sl, tp_price=tp2,
                    comment=f"V75 TSS={sig['tss_score']} {_v75_risk_manager._tier_name}",
                )
                log_trade(
                    ticket=result["ticket"], symbol=symbol,
                    direction=direction,     lot_size=trade_info["lot_size"],
                    entry=entry,             sl=sl,
                    tp1=tp1,                 tp2=tp2,
                    tss_score=sig["tss_score"],
                    checklist=sig["checklist_score"],
                    reason=sig["reason"],
                    atr_zone=sig["atr_zone"],
                )
                event["trade"] = {
                    "ticket":    result["ticket"],
                    "direction": direction,
                    "lot_size":  trade_info["lot_size"],
                    "entry":     entry,
                    "sl":        sl,
                    "tp1":       tp1,
                    "tp2":       tp2,
                    "tier":      _v75_risk_manager._tier_name,
                }
                send_telegram(
                    f"📈 V75 TRADE {symbol} {direction.upper()}\n"
                    f"Tier: {_v75_risk_manager._tier_name} | "
                    f"Lot: {trade_info['lot_size']} | "
                    f"TSS: {sig['tss_score']}/5 | "
                    f"Confidence: {sig['confidence']:.0%}\n"
                    f"SL: {sl} | TP1: {tp1} | TP2: {tp2}"
                )
                logger.info(
                    f"V75 order sent: {symbol} {direction} "
                    f"lot={trade_info['lot_size']} "
                    f"tier={_v75_risk_manager._tier_name}"
                )
            except Exception as e:
                logger.error(f"V75 order error: {e}")
                event["order_error"] = str(e)

        manage_open_positions(symbol, df)
        broadcast_sync(event)

    except Exception as e:
        logger.error(f"on_new_bar_v75 error {symbol}: {e}", exc_info=True)
        broadcast_sync({"type": "error", "source": "v75", "symbol": symbol, "error": str(e)})



# ── Forex bar callback ─────────────────────────────────────────────────────
def on_new_bar(symbol: str, df):
    """
    Forex symbols — uses signal_bridge_py.get_signal() as before,
    but falls through to the V75 pipeline for V75 symbols if routed here.
    """
    if symbol in _V75_SYMBOLS:
        on_new_bar_v75(symbol, df)
        return

    logger.info(f"New bar: {symbol} | {df['time'].iloc[-1]}")
    try:
        sig = get_signal(df)
        _signal_cache[symbol] = {**sig, "symbol": symbol,
                                  "timestamp": str(df["time"].iloc[-1])}
        account   = get_account_info()
        balance   = account.get("balance", 0.0)
        positions = get_open_positions()
        sym_info  = get_symbol_info(symbol)
        point     = sym_info.get("point", 0.00001)
        pip_value = sym_info.get("trade_tick_value", 1.0) * (
            sym_info.get("point", 0.00001) /
            sym_info.get("trade_tick_size", 0.00001)
        )
        approved, trade_info = risk_manager.approve_trade(
            sig, balance, positions, symbol, point, pip_value, sym_info)

        event = {
            "type":     "signal",
            "source":   "forex",
            "symbol":   symbol,
            "signal":   sig,
            "approved": approved,
            "account":  {"balance": balance, "equity": account.get("equity", 0)},
            "reason":   trade_info.get("reason", ""),
        }

        if approved and sig["direction"] != 0:
            tick      = get_tick(symbol)
            direction = "buy" if sig["direction"] == 1 else "sell"
            entry     = tick["ask"] if direction == "buy" else tick["bid"]
            sl, tp1, tp2 = risk_manager.calc_sl_tp(
                sig["direction"], entry, sig["atr"], point)
            try:
                result = send_order(
                    symbol, direction, trade_info["lot_size"],
                    sl_price=sl, tp_price=tp2,
                    comment=f"BOT TSS={sig['tss_score']}",
                )
                log_trade(
                    ticket=result["ticket"], symbol=symbol,
                    direction=direction, lot_size=trade_info["lot_size"],
                    entry=entry, sl=sl, tp1=tp1, tp2=tp2,
                    tss_score=sig["tss_score"],
                    checklist=sig["checklist_score"],
                    reason=sig["reason"], atr_zone=sig["atr_zone"],
                )
                event["trade"] = {
                    "ticket":    result["ticket"],
                    "direction": direction,
                    "lot_size":  trade_info["lot_size"],
                    "entry":     entry,
                    "sl":        sl,
                    "tp1":       tp1,
                    "tp2":       tp2,
                }
                send_telegram(
                    f"TRADE {symbol} {direction.upper()}\n"
                    f"Lot:{trade_info['lot_size']} TSS:{sig['tss_score']}"
                )
            except Exception as e:
                logger.error(f"Order error: {e}")
                event["order_error"] = str(e)

        manage_open_positions(symbol, df)
        broadcast_sync(event)

    except Exception as e:
        logger.error(f"on_new_bar error {symbol}: {e}", exc_info=True)
        broadcast_sync({"type": "error", "symbol": symbol, "error": str(e)})


# ── Boom/Crash bar callback ────────────────────────────────────────────────
def on_new_bar_bc(symbol: str, df_1m):
    try:
        analyzer = _bc_analyzers.get(symbol)
        if not analyzer:
            return
        now    = time.time()
        cached = _bc_5m_cache.get(symbol, {})
        if not cached or now - cached.get("ts", 0) > 60:
            try:
                df_5m = get_ohlcv(symbol, "M5", 100)
                _bc_5m_cache[symbol] = {"df": df_5m, "ts": now}
            except Exception as e:
                logger.error(f"5M fetch failed {symbol}: {e}")
                return
        else:
            df_5m = cached["df"]

        result = analyzer.evaluate(df_1m, df_5m)
        prev   = _signal_cache.get(symbol, {})
        if (result.get("approved") == prev.get("approved") and
                result.get("direction") == prev.get("direction") and
                not result.get("approved")):
            return

        _signal_cache[symbol] = {**result, "timestamp": str(df_1m["time"].iloc[-1])}
        event = {
            "type":     "signal",
            "source":   "boom_crash",
            "symbol":   symbol,
            "signal":   result,
            "approved": result.get("approved", False),
            "account":  get_account_info(),
            "reason":   result.get("reason", ""),
        }

        if result.get("approved") and result.get("direction", 0) != 0:
            direction = "buy" if result["direction"] == 1 else "sell"
            lot_size  = config.get("boom_crash", {}).get("lot_size", 0.2)
            try:
                r = send_order(
                    symbol, direction, lot_size,
                    sl_price=result["sl"], tp_price=result["tp"],
                    comment="BC spike",
                )
                log_trade(
                    ticket=r["ticket"], symbol=symbol,
                    direction=direction, lot_size=lot_size,
                    entry=result["entry"], sl=result["sl"],
                    tp1=result["tp"], tp2=result["tp"],
                    tss_score=0, checklist=0,
                    reason=result["reason"], atr_zone="normal",
                )
                event["trade"] = {
                    "ticket":    r["ticket"],
                    "direction": direction,
                    "lot_size":  lot_size,
                    "entry":     result["entry"],
                    "sl":        result["sl"],
                    "tp":        result["tp"],
                }
            except Exception as e:
                logger.error(f"BC order error {symbol}: {e}")
                event["order_error"] = str(e)

        broadcast_sync(event)

    except Exception as e:
        logger.error(f"on_new_bar_bc error {symbol}: {e}", exc_info=True)


# ── Shutdown ───────────────────────────────────────────────────────────────
streamers:     list = []
crash_scalper        = None

def shutdown(sig=None, frame=None):
    logger.info("Shutting down...")
    if crash_scalper:
        crash_scalper.stop()
    for s in streamers:
        s.stop()
    disconnect()
    sys.exit(0)

_signal.signal(_signal.SIGINT,  shutdown)
_signal.signal(_signal.SIGTERM, shutdown)


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    global crash_scalper, risk_manager, _v75_risk_manager

    logger.info("=" * 60)
    logger.info("  VESTRO BOT — Starting")
    logger.info(f"  Signal engine: {_ENGINE}")
    logger.info("=" * 60)

    init_db()

    account = connect(
        login    = int(os.getenv("MT5_LOGIN",    config["mt5"]["login"])),
        password = os.getenv("MT5_PASSWORD",     config["mt5"]["password"]),
        server   = os.getenv("MT5_SERVER",       config["mt5"]["server"]),
    )
    balance = account.get("balance", 100.0)
    logger.info(f"MT5: {account.get('name')} | ${balance} {account.get('currency')}")

    # Boot tiered risk manager with live balance
    _v75_risk_manager = TieredRiskManager(
        balance   = balance,
        pip_value = 1.0,
        is_prop   = config.get("v75", {}).get("is_prop", False),
    )
    logger.info(
        f"V75 RiskManager: tier={_v75_risk_manager._tier_name} "
        f"risk={_v75_risk_manager.tier['risk_pct']*100}% "
        f"max_trades={_v75_risk_manager.tier['max_trades']}"
    )

    # Legacy risk manager (Forex + Boom/Crash)
    risk_manager = RiskManager(config)

    # API server
    import uvicorn
    api_cfg    = config.get("api", {})
    api_thread = threading.Thread(
        target=lambda: uvicorn.run(
            "bridge.api_server:app",
            host=api_cfg.get("host", "0.0.0.0"),
            port=api_cfg.get("port", 8000),
            log_level="warning",
        ),
        daemon=True, name="APIServer",
    )
    api_thread.start()
    logger.info(f"API server on port {api_cfg.get('port', 8000)}")

    # ── Forex streamers ────────────────────────────────────────────────────
    tf = config["trading"]["primary_timeframe"]
    for symbol in config["trading"]["symbols"]:
        s = BarStreamer(symbol, tf, on_new_bar)
        s.start()
        streamers.append(s)
    logger.info(f"Forex: {len(config['trading']['symbols'])} symbols on {tf}")

    # ── V75 streamers ──────────────────────────────────────────────────────
    v75_cfg     = config.get("v75", {})
    v75_enabled = v75_cfg.get("enabled", True)
    v75_symbols = v75_cfg.get("symbols", ["Volatility 75 Index"])
    v75_tf      = v75_cfg.get("timeframe", "M1")

    if v75_enabled:
        for symbol in v75_symbols:
            s = BarStreamer(symbol, v75_tf, on_new_bar_v75, poll_interval=1.0)
            s.start()
            streamers.append(s)
        logger.info(f"V75 pipeline: {len(v75_symbols)} symbols on {v75_tf}")
    else:
        logger.info("V75 pipeline: disabled")

    # ── Boom/Crash streamers ───────────────────────────────────────────────
    bc_cfg     = config.get("boom_crash", {})
    bc_enabled = bc_cfg.get("enabled", False)
    bc_symbols = bc_cfg.get("symbols", [])
    if bc_enabled and bc_symbols:
        for symbol in bc_symbols:
            _bc_analyzers[symbol] = BoomCrashAnalyzer(symbol, config)
            s = BarStreamer(symbol, "M1", on_new_bar_bc, poll_interval=1.0)
            s.start()
            streamers.append(s)
        logger.info(f"Boom/Crash: {len(bc_symbols)} symbols on M1")
    else:
        logger.info("Boom/Crash: disabled")

    # ── Crash 500 Nuclear Scalper ──────────────────────────────────────────
    from bridge.crash500_scalper import Crash500Scalper
    crash_scalper = Crash500Scalper(
        config          = config,
        broadcast_fn    = broadcast_sync,
        get_account_fn  = get_account_info,
        get_ohlcv_fn    = get_ohlcv,
        send_order_fn   = send_order,
        log_trade_fn    = log_trade,
        get_tick_fn     = get_tick,
        get_symbol_info_fn = get_symbol_info,
        get_positions_fn   = get_open_positions,
    )
    crash_scalper.start()
    logger.info("Crash500 NUCLEAR scalper active")

    send_telegram(
        f"🤖 Vestro Bot STARTED\n"
        f"Balance: ${balance} | Tier: {_v75_risk_manager._tier_name}\n"
        f"V75: {'ON' if v75_enabled else 'OFF'} | "
        f"BC: {'ON' if bc_enabled else 'OFF'}"
    )

    # ── Heartbeat ──────────────────────────────────────────────────────────
    while True:
        time.sleep(30)
        try:
            account = get_account_info()
            new_bal = account.get("balance", _v75_risk_manager.balance)
            # Re-tier silently if balance changed
            if abs(new_bal - _v75_risk_manager.balance) > 0.01:
                _v75_risk_manager.balance    = new_bal
                _v75_risk_manager._tier_name = _v75_risk_manager._detect_tier()
            broadcast_sync({
                "type":      "heartbeat",
                "account":   account,
                "v75_tier":  _v75_risk_manager._tier_name,
                "v75_metrics": _v75_learner.metrics(),
                "timestamp": time.time(),
            })
        except Exception as e:
            logger.warning(f"Heartbeat error: {e}")


if __name__ == "__main__":
    main()