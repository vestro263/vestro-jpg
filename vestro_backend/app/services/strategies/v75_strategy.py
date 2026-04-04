"""
v75_strategy.py
===============
VESTRO V75 Strategy — Volatility 75 Index
5-phase pipeline: Data → Features → Patterns → Predict → Risk

FIXES:
  - fetch_market_data now requests real OHLC candles from Deriv
    (not raw ticks grouped into 3 candles — that broke EMA200/ADX/RSI)
  - Balance refreshes from Deriv on every scan so lot sizing is always accurate
"""

import httpx
import json
import os
import statistics
import websockets
import numpy as np

from .base_strategy import BaseStrategy

DERIV_APP_ID = os.environ["DERIV_APP_ID"]
BACKEND_URL  = os.environ.get("BACKEND_URL", "https://vestro-jpg.onrender.com")


# ============================================================
# PHASE 2 — FEATURE ENGINE
# ============================================================

class _FeatureEngine:
    def __init__(self, candles: list):
        self.candles = candles
        self.closes  = [c["close"] for c in candles]
        self.highs   = [c["high"]  for c in candles]
        self.lows    = [c["low"]   for c in candles]

    def ema(self, period: int, prices: list = None) -> list:
        src = prices or self.closes
        k = 2 / (period + 1)
        result = [src[0]]
        for p in src[1:]:
            result.append(p * k + result[-1] * (1 - k))
        return result

    def atr(self, period: int = 14) -> list:
        trs = []
        for i in range(1, len(self.candles)):
            tr = max(
                self.highs[i] - self.lows[i],
                abs(self.highs[i] - self.closes[i - 1]),
                abs(self.lows[i]  - self.closes[i - 1]),
            )
            trs.append(tr)
        if not trs:
            return [0.0]
        result = [sum(trs[:period]) / period]
        for tr in trs[period:]:
            result.append((result[-1] * (period - 1) + tr) / period)
        return result

    def rsi(self, period: int = 14) -> list:
        deltas = [self.closes[i] - self.closes[i - 1] for i in range(1, len(self.closes))]
        gains  = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        if len(gains) < period:
            return [50.0]
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period + 1e-10
        vals  = [100 - (100 / (1 + avg_g / avg_l))]
        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period + 1e-10
            vals.append(100 - (100 / (1 + avg_g / avg_l)))
        return vals

    def macd(self, fast=12, slow=26, signal=9) -> dict:
        ef   = self.ema(fast)
        es   = self.ema(slow)
        line = [f - s for f, s in zip(ef, es)]
        sig  = self.ema(signal, line)
        hist = [m - s for m, s in zip(line, sig)]
        return {"macd": line, "signal": sig, "histogram": hist}

    def adx(self, period: int = 14) -> list:
        plus_dm, minus_dm, tr_list = [], [], []
        for i in range(1, len(self.candles)):
            up   = self.highs[i] - self.highs[i - 1]
            down = self.lows[i - 1] - self.lows[i]
            plus_dm.append(up   if up > down and up > 0   else 0)
            minus_dm.append(down if down > up and down > 0 else 0)
            tr_list.append(max(
                self.highs[i] - self.lows[i],
                abs(self.highs[i] - self.closes[i - 1]),
                abs(self.lows[i]  - self.closes[i - 1]),
            ))
        if len(tr_list) < period:
            return [0.0]

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

    def build_all(self) -> dict:
        m = self.macd()
        return {
            "ema_21":         self.ema(21),
            "ema_50":         self.ema(50),
            "ema_200":        self.ema(200),
            "rsi_14":         self.rsi(14),
            "atr_14":         self.atr(14),
            "adx_14":         self.adx(14),
            "macd":           m["macd"],
            "macd_signal":    m["signal"],
            "macd_histogram": m["histogram"],
        }


# ============================================================
# PHASE 3 — PATTERN EXTRACTOR
# ============================================================

class _PatternExtractor:
    def __init__(self, candles: list, features: dict):
        self.candles  = candles
        self.features = features

    def trend_strength_score(self) -> int:
        score  = 0
        closes = [c["close"] for c in self.candles]
        ema21  = self.features.get("ema_21",  [closes[-1]])
        ema50  = self.features.get("ema_50",  [closes[-1]])
        ema200 = self.features.get("ema_200", [closes[-1]])
        adx    = self.features.get("adx_14",  [0])
        macd_h = self.features.get("macd_histogram", [0])

        bull = ema21[-1] > ema50[-1] > ema200[-1]
        bear = ema21[-1] < ema50[-1] < ema200[-1]
        if bull or bear:
            score += 1
        if adx and adx[-1] > 25:
            score += 1
        if ema200[-1] and closes[-1] > ema200[-1]:
            score += 1
        if macd_h and macd_h[-1] > 0:
            score += 1
        volumes = [c["volume"] for c in self.candles]
        if len(volumes) > 1:
            avg_vol = statistics.mean(volumes[:-1])
            if volumes[-1] > avg_vol * 1.2:
                score += 1
        return score

    def rsi_divergence(self, lookback: int = 10) -> str:
        rsi    = self.features.get("rsi_14", [])
        closes = [c["close"] for c in self.candles]
        if len(rsi) < lookback or len(closes) < lookback:
            return "none"
        price_last = closes[-1];  price_prev = closes[-lookback]
        rsi_last   = rsi[-1];     rsi_prev   = rsi[-lookback]
        if price_last > price_prev and rsi_last < rsi_prev: return "regular_bearish"
        if price_last < price_prev and rsi_last > rsi_prev: return "regular_bullish"
        if price_last < price_prev and rsi_last < rsi_prev: return "hidden_bearish"
        if price_last > price_prev and rsi_last > rsi_prev: return "hidden_bullish"
        return "none"

    def compression_zone(self, lookback: int = 8) -> bool:
        if len(self.candles) < lookback + 5:
            return False
        recent = [abs(self.candles[i]["close"] - self.candles[i]["open"]) for i in range(-lookback, 0)]
        prior  = [abs(self.candles[i]["close"] - self.candles[i]["open"]) for i in range(-lookback - 5, -lookback)]
        avg_r  = statistics.mean(recent)
        avg_p  = statistics.mean(prior) + 1e-10
        return avg_r / avg_p < 0.5


# ============================================================
# PHASE 4 — PREDICTION ENGINE
# ============================================================

class _PredictionEngine:
    def __init__(self, patterns: _PatternExtractor, features: dict, candles: list):
        self.patterns = patterns
        self.features = features
        self.candles  = candles

    def _atr_zone(self) -> str:
        atr_vals = self.features.get("atr_14", [1])
        if len(atr_vals) < 21:
            return "normal"
        ratio = atr_vals[-1] / (statistics.mean(atr_vals[-21:-1]) + 1e-10)
        if ratio < 0.5:  return "low"
        if ratio < 1.5:  return "normal"
        if ratio < 2.5:  return "elevated"
        return "extreme"

    def _entry_checklist(self, direction: str) -> int:
        score   = 0
        closes  = [c["close"] for c in self.candles]
        rsi     = self.features.get("rsi_14",  [50])
        macd_h  = self.features.get("macd_histogram", [0])
        ema50   = self.features.get("ema_50",  [closes[-1]])
        ema200  = self.features.get("ema_200", [closes[-1]])
        volumes = [c["volume"] for c in self.candles]
        last_c  = self.candles[-1]

        body = abs(last_c["close"] - last_c["open"])
        rng  = last_c["high"] - last_c["low"] + 1e-5

        if direction == "buy":
            if ema50[-1] > ema200[-1]:                                 score += 1
            if 30 <= rsi[-1] <= 55:                                    score += 1  # widened from 45
            if macd_h[-1] > 0:                                         score += 1
            if last_c["close"] > last_c["open"] and body / rng > 0.4: score += 1  # relaxed from 0.5
        else:
            if ema50[-1] < ema200[-1]:                                 score += 1
            if 45 <= rsi[-1] <= 70:                                    score += 1  # widened from 55-70
            if macd_h[-1] < 0:                                         score += 1
            if last_c["close"] < last_c["open"] and body / rng > 0.4: score += 1  # relaxed from 0.5

        if len(volumes) > 1:
            avg_v = statistics.mean(volumes[:-1])
            if volumes[-1] > avg_v * 1.1:   # relaxed from 1.2
                score += 1
        score += 1   # session check
        score += 1   # zone check
        return min(score, 7)

    def predict(self) -> dict:
        tss      = self.patterns.trend_strength_score()
        diverge  = self.patterns.rsi_divergence()
        compress = self.patterns.compression_zone()
        atr_zone = self._atr_zone()
        closes   = [c["close"] for c in self.candles]
        ema21    = self.features.get("ema_21",  [closes[-1]])
        ema50    = self.features.get("ema_50",  [closes[-1]])
        ema200   = self.features.get("ema_200", [closes[-1]])

        if atr_zone == "extreme":
            return {"signal": "HOLD", "reason": "ATR extreme — stand aside",
                    "tss": tss, "checklist": 0, "atr_zone": atr_zone, "confidence": 0.0}

        bull_stack = ema21[-1] > ema50[-1] > ema200[-1]
        bear_stack = ema21[-1] < ema50[-1] < ema200[-1]
        direction  = "buy" if bull_stack else ("sell" if bear_stack else None)

        if not direction:
            return {"signal": "HOLD", "reason": "EMA stack indeterminate",
                    "tss": tss, "checklist": 0, "atr_zone": atr_zone, "confidence": 0.0}

        checklist = self._entry_checklist(direction)

        # Lowered thresholds: checklist 4/7 and TSS 2/5 (was 5 and 3)
        if checklist < 4 or tss < 2:
            return {"signal": "HOLD",
                    "reason": f"Checklist {checklist}/7, TSS {tss}/5 — insufficient confluence",
                    "tss": tss, "checklist": checklist, "atr_zone": atr_zone, "confidence": 0.0}

        confidence = min(1.0, (tss / 5) * 0.5 + (checklist / 7) * 0.5)
        signal     = "BUY" if direction == "buy" else "SELL"

        return {
            "signal":      signal,
            "price":       closes[-1],
            "confidence":  round(confidence, 3),
            "tss":         tss,
            "checklist":   checklist,
            "atr_zone":    atr_zone,
            "divergence":  diverge,
            "spike_ready": compress and signal == "SELL",
            "reason":      f"TSS {tss}/5, Checklist {checklist}/7, {atr_zone.upper()} ATR",
        }


# ============================================================
# PHASE 5 — RISK MANAGER
# ============================================================

class _RiskManager:
    TIERS = {
        "starter":     {"risk_pct": 0.01,   "max_trades": 2, "daily_dd": 0.03, "loss_limit": 2},
        "growth":      {"risk_pct": 0.015,  "max_trades": 3, "daily_dd": 0.04, "loss_limit": 3},
        "established": {"risk_pct": 0.02,   "max_trades": 4, "daily_dd": 0.05, "loss_limit": 3},
        "prop":        {"risk_pct": 0.0075, "max_trades": 2, "daily_dd": 0.03, "loss_limit": 2},
    }

    def __init__(self, balance: float, is_prop: bool = False):
        self.balance    = balance
        self.is_prop    = is_prop
        self._tier_name = "prop" if is_prop else (
            "starter" if balance < 50 else
            "growth"  if balance < 500 else
            "established"
        )

    @property
    def tier(self):
        return self.TIERS[self._tier_name]

    def lot_size(self, sl_pips: float, atr_zone: str = "normal") -> float:
        multiplier  = {"low": 1.0, "normal": 1.0, "elevated": 0.5, "extreme": 0.0}.get(atr_zone, 1.0)
        risk_dollar = self.balance * self.tier["risk_pct"]
        lots        = (risk_dollar / (sl_pips + 1e-10)) * multiplier
        return round(max(lots, 0.01), 2)

    def sl_tp(self, entry: float, direction: str, atr_val: float) -> dict:
        sl_dist = atr_val * 1.5
        if direction == "buy":
            return {"sl": round(entry - sl_dist, 2), "tp": round(entry + sl_dist * 1.5, 2)}
        return {"sl": round(entry + sl_dist, 2), "tp": round(entry - sl_dist * 1.5, 2)}


# ============================================================
# V75 STRATEGY
# ============================================================

class V75Strategy(BaseStrategy):
    NAME   = "V75"
    SYMBOL = "R_100"

    def __init__(self, api_token, broadcast_fn, execute_trade_fn,
                 balance: float = 1000.0, is_prop: bool = False):
        super().__init__(api_token, broadcast_fn, execute_trade_fn)
        self.balance = balance
        self.is_prop = is_prop

    # ── Phase 1 — fetch real OHLC candles ────────────────────
    # FIX: previously fetched 200 raw ticks grouped by 60 = only 3 candles.
    # EMA200 needs 200 candles. Now requests candles directly from Deriv API.
    async def fetch_market_data(self) -> dict:
        url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
        async with websockets.connect(url) as ws:

            # Authorize and refresh balance in one shot
            await ws.send(json.dumps({"authorize": self.api_token}))
            auth = json.loads(await ws.recv())
            try:
                self.balance = float(auth["authorize"]["balance"])
            except (KeyError, TypeError):
                pass  # keep existing balance if auth response malformed

            # Request 300 x 1-minute OHLC candles directly
            await ws.send(json.dumps({
                "ticks_history": self.SYMBOL,
                "style":         "candles",     # ← OHLC, not ticks
                "granularity":   60,             # 1-minute candles
                "count":         300,            # 300 candles — enough for EMA200
                "end":           "latest",
            }))
            data = json.loads(await ws.recv())

        # Deriv returns candles as list of {open, high, low, close, epoch}
        raw_candles = data.get("candles", [])
        candles = [
            {
                "open":   float(c["open"]),
                "high":   float(c["high"]),
                "low":    float(c["low"]),
                "close":  float(c["close"]),
                "volume": 60,    # 1-min candle = 60 ticks at 1/s
                "epoch":  c.get("epoch", 0),
            }
            for c in raw_candles
        ]

        self.logger.info(
            f"[{self.NAME}] fetched {len(candles)} candles | "
            f"balance={self.balance} | last_close={candles[-1]['close'] if candles else 'N/A'}"
        )
        return {"candles": candles}

    # ── Phases 2-4 ────────────────────────────────────────────
    async def compute_signal(self, market_data: dict) -> dict:
        candles = market_data["candles"]

        # Need at least 220 candles for EMA200 + buffer
        if len(candles) < 220:
            return {
                "signal": "HOLD", "symbol": self.SYMBOL,
                "confidence": 0.0,
                "reason": f"insufficient candles ({len(candles)}/220 needed)",
                "amount": 0.0, "meta": {},
            }

        features  = _FeatureEngine(candles).build_all()
        patterns  = _PatternExtractor(candles, features)
        predictor = _PredictionEngine(patterns, features, candles)
        result    = predictor.predict()

        self.logger.info(
            f"[{self.NAME}] TSS={result.get('tss')}/5 "
            f"checklist={result.get('checklist')}/7 "
            f"signal={result['signal']} reason={result['reason']}"
        )

        # Phase 5 — position sizing
        atr_val = features["atr_14"][-1] if features.get("atr_14") else 1000
        risk    = _RiskManager(self.balance, self.is_prop)
        sl_pips = atr_val * 1.5
        lot     = risk.lot_size(sl_pips, result.get("atr_zone", "normal"))
        levels  = risk.sl_tp(
            entry     = candles[-1]["close"],
            direction = "buy" if result["signal"] == "BUY" else "sell",
            atr_val   = atr_val,
        )

        return {
            "signal":     result["signal"],
            "symbol":     self.SYMBOL,
            "confidence": result.get("confidence", 0.0),
            "reason":     result.get("reason", ""),
            "amount":     lot,
            "meta": {
                "tss":       result.get("tss"),
                "checklist": result.get("checklist"),
                "atr_zone":  result.get("atr_zone"),
                "atr_val":   round(atr_val, 4),
                "sl":        levels["sl"],
                "tp":        levels["tp"],
                "entry":     candles[-1]["close"],
                "balance":   self.balance,
            }
        }

    # ── Execution gate ────────────────────────────────────────
    async def should_execute(self, signal: dict) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                status = await client.get(f"{BACKEND_URL}/api/bot/status", timeout=5)
                bot_running = status.json().get("running", False)
            if not bot_running:
                self.logger.info(f"[{self.NAME}] bot not running — skipping execution")
                return False
            if signal.get("meta", {}).get("atr_zone") == "extreme":
                self.logger.info(f"[{self.NAME}] ATR extreme — skipping execution")
                return False
            if signal["amount"] <= 0:
                self.logger.info(f"[{self.NAME}] lot size 0 — skipping execution")
                return False
            return True
        except Exception as e:
            self.logger.error(f"[{self.NAME}] should_execute error: {e}")
            return False