"""
v25_strategy.py  (upgraded)
=============================
VESTRO V25 Strategy — Volatility 25 Index
5-phase pipeline: Data → Features → Patterns → Predict → Risk

Changes vs previous version
-----------------------------
[REGIME-GATE]      compute_signal() reads current market regime from
                   signal_engine via get_current_regime() and applies:

                   CRASH    → HOLD all BUY signals immediately.
                              SELL still evaluated (crash = downtrend).
                   HIGH_VOL → Suppress BUY; SELL still evaluated.
                   RANGE    → Both directions evaluated but checklist
                              tightened +1 via effective_checklist_min().
                   TREND    → Normal pipeline, no change.
                   UNKNOWN  → Fail-open (no gate on first boot).

[REGIME-THRESHOLD] Uses t.effective_checklist_min() so RANGE regime
                   tightening is handled in calibration_loader without
                   duplicating logic here.

[REGIME-LOG]       regime written to meta dict and broadcast payload so
                   SignalLog rows carry regime context for outcome_labeler
                   and per-regime precision analysis in walk_forward folds.

All V25-specific differences preserved unchanged:
  [V25-DIFF-1]  SYMBOL = "R_25"
  [V25-DIFF-2]  Tighter ATR zone thresholds (V25 moves ~1/3 as fast as V75)
  [V25-DIFF-3]  RSI bounds narrowed (rsi_buy_max=50, rsi_sell_min=50)
  [V25-DIFF-4]  ADX minimum 20 (V75 is 25)
  [V25-DIFF-5]  Confidence weights: w_tss=0.45, w_chk=0.55
  [V25-DIFF-6]  Cooldown 90 s (V75 is 120 s)
  [V25-DIFF-7]  Prop daily_dd 0.02
  [V25-DIFF-8]  Stake cap $8.00 (V75 is $10.00)
  [V25-DIFF-9]  checklist_min=3, tss_min=2 (same as V75 post HOLD-FIX)
"""

import httpx
import json
import os
import statistics
import websockets

from .base_strategy import BaseStrategy
from app.services.regime_cache import get_current_regime          # ← regime gate

DERIV_APP_ID = os.environ["DERIV_APP_ID"]
BACKEND_URL  = os.environ.get("BACKEND_URL", "https://vestro-jpg.onrender.com")


# ============================================================
# PHASE 2 — FEATURE ENGINE  (unchanged from original v25)
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
# PHASE 3 — PATTERN EXTRACTOR  (unchanged from original v25)
# ============================================================

class _PatternExtractor:
    def __init__(self, candles: list, features: dict, thresholds):
        self.candles    = candles
        self.features   = features
        self.thresholds = thresholds

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

        # [V25-DIFF-4] adx_min default 20 for V25
        adx_min = getattr(self.thresholds, "adx_min", 20)
        if adx and adx[-1] > adx_min:
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
# PHASE 4 — PREDICTION ENGINE  (regime-threshold wired in)
# ============================================================

class _PredictionEngine:
    def __init__(self, patterns: _PatternExtractor, features: dict, candles: list, thresholds):
        self.patterns   = patterns
        self.features   = features
        self.candles    = candles
        self.thresholds = thresholds

    def _atr_zone(self) -> str:
        atr_vals = self.features.get("atr_14", [1])
        if len(atr_vals) < 21:
            return "normal"
        ratio = atr_vals[-1] / (statistics.mean(atr_vals[-21:-1]) + 1e-10)
        # [V25-DIFF-2] Tighter ATR zone thresholds for V25
        if ratio < 0.5:  return "low"
        if ratio < 1.3:  return "normal"    # V75 uses 1.5
        if ratio < 2.0:  return "elevated"  # V75 uses 2.5
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

        # [V25-DIFF-3] RSI thresholds tighter for V25
        rsi_buy_max    = getattr(self.thresholds, "rsi_buy_max",  50)
        rsi_sell_min   = getattr(self.thresholds, "rsi_sell_min", 50)
        body_ratio_min = getattr(self.thresholds, "body_ratio_min", 0.4)
        vol_mult       = getattr(self.thresholds, "volume_spike_mult", 1.1)

        if direction == "buy":
            if ema50[-1] > ema200[-1]:                                          score += 1
            if rsi[-1] <= rsi_buy_max:                                          score += 1
            if macd_h[-1] > 0:                                                  score += 1
            if last_c["close"] > last_c["open"] and body / rng > body_ratio_min: score += 1
        else:
            if ema50[-1] < ema200[-1]:                                          score += 1
            if rsi[-1] >= rsi_sell_min:                                         score += 1
            if macd_h[-1] < 0:                                                  score += 1
            if last_c["close"] < last_c["open"] and body / rng > body_ratio_min: score += 1

        if len(volumes) > 1:
            avg_v = statistics.mean(volumes[:-1])
            if volumes[-1] > avg_v * vol_mult:
                score += 1

        score += 1   # session check
        score += 1   # zone check
        return min(score, 7)

    def predict(self, effective_checklist_min: int = None) -> dict:
        tss      = self.patterns.trend_strength_score()
        diverge  = self.patterns.rsi_divergence()
        compress = self.patterns.compression_zone()
        atr_zone = self._atr_zone()

        closes = [c["close"] for c in self.candles]
        ema21  = self.features.get("ema_21",  [closes[-1]])
        ema50  = self.features.get("ema_50",  [closes[-1]])
        ema200 = self.features.get("ema_200", [closes[-1]])

        blocked_zones = getattr(self.thresholds, "blocked_atr_zones", ["extreme"])
        if atr_zone in blocked_zones:
            return {
                "signal": "HOLD", "reason": f"ATR zone '{atr_zone}' blocked",
                "tss": tss, "checklist": 0, "atr_zone": atr_zone, "confidence": 0.0,
            }

        # 2-of-3 EMA majority vote (HOLD-FIX-1, same as V75)
        ema21_v  = ema21[-1]
        ema50_v  = ema50[-1]
        ema200_v = ema200[-1]

        bull_points = sum([ema21_v > ema50_v, ema50_v > ema200_v, ema21_v > ema200_v])
        bear_points = sum([ema21_v < ema50_v, ema50_v < ema200_v, ema21_v < ema200_v])

        if bull_points >= 2:
            direction = "buy"
        elif bear_points >= 2:
            direction = "sell"
        else:
            return {
                "signal": "HOLD", "reason": "EMA stack indeterminate (tied 1-1-1)",
                "tss": tss, "checklist": 0, "atr_zone": atr_zone, "confidence": 0.0,
            }

        checklist = self._entry_checklist(direction)

        # [REGIME-THRESHOLD] use regime-aware checklist minimum when passed in
        min_checklist = effective_checklist_min if effective_checklist_min is not None \
                        else getattr(self.thresholds, "checklist_min", 3)
        min_tss       = getattr(self.thresholds, "tss_min", 2)

        if checklist < min_checklist or tss < min_tss:
            return {
                "signal": "HOLD",
                "reason": f"Checklist {checklist}/{min_checklist}, TSS {tss}/{min_tss} — filtered",
                "tss": tss, "checklist": checklist, "atr_zone": atr_zone, "confidence": 0.0,
            }

        # [V25-DIFF-5] Checklist weighted slightly higher on V25
        w_tss = getattr(self.thresholds, "w_tss",       0.45)
        w_chk = getattr(self.thresholds, "w_checklist", 0.55)
        confidence = min(1.0, (tss / 5) * w_tss + (checklist / 7) * w_chk)

        min_conf = getattr(self.thresholds, "confidence_min", 0.0)
        if confidence < min_conf:
            return {
                "signal": "HOLD",
                "reason": f"confidence {confidence:.2f} < floor {min_conf}",
                "tss": tss, "checklist": checklist, "atr_zone": atr_zone,
                "confidence": confidence,
            }

        signal = "BUY" if direction == "buy" else "SELL"
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
# PHASE 5 — RISK MANAGER  (unchanged from original v25)
# ============================================================

class _RiskManager:
    TIERS = {
        "starter":     {"risk_pct": 0.01,   "max_trades": 2, "daily_dd": 0.03, "loss_limit": 2},
        "growth":      {"risk_pct": 0.015,  "max_trades": 3, "daily_dd": 0.04, "loss_limit": 3},
        "established": {"risk_pct": 0.02,   "max_trades": 4, "daily_dd": 0.05, "loss_limit": 3},
        # [V25-DIFF-7] prop daily_dd tightened to 0.02
        "prop":        {"risk_pct": 0.0075, "max_trades": 2, "daily_dd": 0.02, "loss_limit": 2},
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
        # [V25-DIFF-2] sl_atr_mult default 1.2 (V75 uses 1.5)
        sl_dist = atr_val * 1.2
        if direction == "buy":
            return {"sl": round(entry - sl_dist, 2), "tp": round(entry + sl_dist * 1.5, 2)}
        return {"sl": round(entry + sl_dist, 2), "tp": round(entry - sl_dist * 1.5, 2)}


# ============================================================
# V25 STRATEGY
# ============================================================

class V25Strategy(BaseStrategy):
    NAME   = "V25"
    SYMBOL = "R_25"          # [V25-DIFF-1]
    _last_executed:    float = 0
    _cooldown_seconds: int   = 90   # [V25-DIFF-6]

    def __init__(self, api_token, broadcast_fn, execute_trade_fn,
                 balance: float = 1000.0, is_prop: bool = False):
        super().__init__(api_token, broadcast_fn, execute_trade_fn)
        self.balance = balance
        self.is_prop = is_prop

    async def fetch_market_data(self) -> dict:
        url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"authorize": self.api_token}))
            auth = json.loads(await ws.recv())
            try:
                self.balance = float(auth["authorize"]["balance"])
            except (KeyError, TypeError):
                pass

            await ws.send(json.dumps({
                "ticks_history": self.SYMBOL,
                "style":         "candles",
                "granularity":   60,
                "count":         300,
                "end":           "latest",
            }))
            data = json.loads(await ws.recv())

        raw_candles = data.get("candles", [])
        candles = [
            {
                "open":   float(c["open"]),
                "high":   float(c["high"]),
                "low":    float(c["low"]),
                "close":  float(c["close"]),
                "volume": 60,
                "epoch":  c.get("epoch", 0),
            }
            for c in raw_candles
        ]
        self.logger.info(
            f"[{self.NAME}] fetched {len(candles)} candles | "
            f"balance={self.balance} | last_close={candles[-1]['close'] if candles else 'N/A'}"
        )
        return {"candles": candles}

    async def compute_signal(self, market_data: dict) -> dict:
        candles = market_data["candles"]

        from ml.calibration_loader import get_thresholds
        t = get_thresholds(self.SYMBOL)

        # ── [REGIME-GATE] Read current regime ─────────────────────────────
        regime = get_current_regime(self.SYMBOL)

        if len(candles) < 220:
            return {
                "signal": "HOLD", "symbol": self.SYMBOL,
                "confidence": 0.0,
                "reason": f"insufficient candles ({len(candles)}/220 needed)",
                "amount": 0.0, "meta": {}, "indicators": {},
            }

        features  = _FeatureEngine(candles).build_all()
        patterns  = _PatternExtractor(candles, features, t)
        predictor = _PredictionEngine(patterns, features, candles, t)

        # [REGIME-THRESHOLD] regime-aware checklist minimum
        effective_chk = t.effective_checklist_min()
        result        = predictor.predict(effective_checklist_min=effective_chk)

        # ── [REGIME-GATE] Suppress after prediction based on regime ───────
        if result["signal"] != "HOLD":
            signal_direction = result["signal"]

            if regime == "CRASH" and signal_direction == "BUY":
                result = {
                    "signal":    "HOLD",
                    "reason":    "CRASH regime — BUY suppressed",
                    "tss":       result.get("tss", 0),
                    "checklist": result.get("checklist", 0),
                    "atr_zone":  result.get("atr_zone", "normal"),
                    "confidence": 0.0,
                }
            elif regime == "HIGH_VOL" and signal_direction == "BUY":
                result = {
                    "signal":    "HOLD",
                    "reason":    "HIGH_VOL regime — BUY suppressed",
                    "tss":       result.get("tss", 0),
                    "checklist": result.get("checklist", 0),
                    "atr_zone":  result.get("atr_zone", "normal"),
                    "confidence": 0.0,
                }
            # RANGE handled via effective_checklist_min; no explicit suppression

        atr_val    = features["atr_14"][-1] if features.get("atr_14") else 1000
        atr_zone   = result.get("atr_zone", "normal")
        tss        = result.get("tss", 0)
        confidence = result.get("confidence", 0.0)

        # Use regime-specific ML model for confidence if available
        from ml.calibration_loader import load_calibrated_model
        if result["signal"] != "HOLD":
            try:
                regime_model = load_calibrated_model(self.SYMBOL, regime)
                if regime_model:
                    feature_vector = [
                        indicators.get("rsi") or 50,
                        indicators.get("adx") or 25,
                        indicators.get("atr") or 0,
                        indicators.get("ema_50") or 0,
                        indicators.get("ema_200") or 0,
                        indicators.get("macd_hist") or 0,
                        tss,
                        result.get("checklist") or 0,
                        confidence,
                    ]
                    proba = regime_model.predict_proba([feature_vector])[0]
                    classes = list(regime_model.classes_)
                    win_idx = classes.index(1) if 1 in classes else -1
                    if win_idx >= 0:
                        ml_conf = float(proba[win_idx])
                        self.logger.info(
                            f"[{self.NAME}] regime model: "
                            f"formula={confidence:.3f} → ml={ml_conf:.3f} regime={regime}"
                        )
                        confidence = ml_conf
            except Exception as e:
                self.logger.warning(f"[{self.NAME}] regime model inference failed: {e}")


        risk    = _RiskManager(self.balance, self.is_prop)
        sl_mult = getattr(t, "sl_atr_mult", 1.2)   # [V25-DIFF-2]
        sl_pips = atr_val * sl_mult
        lot     = risk.lot_size(sl_pips, atr_zone)
        levels  = risk.sl_tp(
            entry     = candles[-1]["close"],
            direction = "buy" if result["signal"] == "BUY" else "sell",
            atr_val   = atr_val,
        )

        indicators = {
            "rsi":       round(features["rsi_14"][-1], 2)         if features.get("rsi_14")         else None,
            "adx":       round(features["adx_14"][-1], 2)         if features.get("adx_14")         else None,
            "atr":       round(atr_val, 5),
            "ema_50":    round(features["ema_50"][-1], 4)         if features.get("ema_50")         else None,
            "ema_200":   round(features["ema_200"][-1], 4)        if features.get("ema_200")        else None,
            "macd_hist": round(features["macd_histogram"][-1], 5) if features.get("macd_histogram") else None,
        }

        await self.broadcast_fn({
            "symbol": self.SYMBOL,
            "action": result["signal"],
            "signal": {
                "direction":  1 if result["signal"] == "BUY" else (-1 if result["signal"] == "SELL" else 0),
                "rsi":        indicators["rsi"]       or 0,
                "adx":        indicators["adx"]       or 0,
                "atr":        indicators["atr"],
                "ema50":      indicators["ema_50"]    or 0,
                "ema200":     indicators["ema_200"]   or 0,
                "macd_hist":  indicators["macd_hist"] or 0,
                "tss_score":  tss,
                "atr_zone":   atr_zone,
                "confidence": confidence,
                "regime":     regime,                 # ← [REGIME-LOG]
                "reason":     result.get("reason", ""),
            }
        })

        # ── Write SignalLog row ────────────────────────────────────────────
        signal_log_id = None
        if result["signal"] != "HOLD":
            from app.database import AsyncSessionLocal
            from ml.signal_log_model import SignalLog
            from datetime import datetime
            try:
                dirval = 1 if result["signal"] == "BUY" else -1
                entry  = candles[-1]["close"]
                row = SignalLog(
                    strategy    = self.NAME,
                    symbol      = self.SYMBOL,
                    signal      = result["signal"],
                    direction   = dirval,
                    entry_price = entry,
                    tp_price    = levels["tp"],
                    sl_price    = levels["sl"],
                    rsi         = indicators.get("rsi"),
                    adx         = indicators.get("adx"),
                    atr         = indicators.get("atr"),
                    ema_50      = indicators.get("ema_50"),
                    ema_200     = indicators.get("ema_200"),
                    macd_hist   = indicators.get("macd_hist"),
                    tss_score   = tss,
                    checklist   = result.get("checklist"),
                    atr_zone    = atr_zone,
                    confidence  = confidence,
                    regime=regime,
                    captured_at = datetime.utcnow(),
                )
                async with AsyncSessionLocal() as db:
                    db.add(row)
                    await db.commit()
                    await db.refresh(row)
                    signal_log_id = row.id
                    self.logger.info(f"[{self.NAME}] SignalLog written: {signal_log_id}")
            except Exception as log_err:
                self.logger.warning(f"[{self.NAME}] SignalLog insert failed: {log_err}")

        return {
            "signal":     result["signal"],
            "symbol":     self.SYMBOL,
            "confidence": confidence,
            "reason":     result.get("reason", ""),
            "amount":     lot if result["signal"] != "HOLD" else 0.0,
            "indicators": indicators,
            "meta": {
                "signal_log_id": signal_log_id,
                "tss":           tss,
                "checklist":     result.get("checklist"),
                "atr_zone":      atr_zone,
                "atr_val":       round(atr_val, 4),
                "sl":            levels["sl"],
                "tp":            levels["tp"],
                "entry":         candles[-1]["close"],
                "balance":       self.balance,
                "regime":        regime,              # ← [REGIME-LOG]
                "thresholds": {
                    "confidence_min":    t.confidence_min,
                    "tss_min":           t.tss_min,
                    "checklist_min":     t.checklist_min,
                    "effective_chk_min": effective_chk,
                    "sl_atr_mult":       getattr(t, "sl_atr_mult", 1.2),
                },
            },
        }

    async def should_execute(self, signal: dict) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                status = await client.get(f"{BACKEND_URL}/api/bot/status", timeout=5)
                bot_running = status.json().get("running", False)

            self.logger.info(
                f"[{self.NAME}] gate check | "
                f"bot={bot_running} "
                f"conf={signal.get('confidence', 0):.3f} "
                f"amount={signal.get('amount', 0):.4f} "
                f"atr={signal.get('meta', {}).get('atr_zone', '?')} "
                f"regime={signal.get('meta', {}).get('regime', '?')}"
            )

            if not bot_running:
                self.logger.info(f"[{self.NAME}] bot not running — skipping")
                return False

            if signal.get("meta", {}).get("atr_zone") == "extreme":
                self.logger.info(f"[{self.NAME}] ATR extreme — skipping")
                return False

            if signal.get("amount", 0) <= 0:
                self.logger.info(f"[{self.NAME}] lot size 0 — skipping")
                return False

            confidence  = signal.get("confidence", 0.0)
            min_conf    = signal.get("meta", {}).get("thresholds", {}).get("confidence_min", 0.0)
            exec_thresh = max(min_conf, 0.60)

            if confidence < exec_thresh:
                self.logger.info(
                    f"[{self.NAME}] confidence {confidence:.3f} < {exec_thresh:.2f} — skipping"
                )
                return False

            return True

        except Exception as e:
            self.logger.error(f"[{self.NAME}] should_execute error: {e}")
            return False

    async def execute(self, signal: dict) -> dict | None:
        if signal["signal"] == "HOLD":
            return None

        action     = "rise" if signal["signal"] == "BUY" else "fall"
        confidence = signal.get("confidence", 0.60)
        meta       = signal.get("meta", {})
        balance    = meta.get("balance", self.balance) or self.balance
        atr_zone   = meta.get("atr_zone", "normal")

        if self.is_prop:
            risk_pct = 0.0075
        elif balance < 50:
            risk_pct = 0.01
        elif balance < 500:
            risk_pct = 0.015
        else:
            risk_pct = 0.02

        atr_mult = {"low": 1.0, "normal": 1.0, "elevated": 0.5, "extreme": 0.0}.get(atr_zone, 1.0)
        if atr_mult == 0.0:
            self.logger.info(f"[{self.NAME}] ATR extreme in execute() — aborting")
            return None

        conf_scale = max(0.5, min(1.5, 1.0 + (confidence - 0.60) * 2.5))
        base_stake = balance * risk_pct
        # [V25-DIFF-8] Cap $8.00 (V75 uses $10.00)
        stake      = round(max(0.35, min(8.0, base_stake * conf_scale * atr_mult)), 2)

        self.logger.info(
            f"[{self.NAME}] EXECUTING {action.upper()} | "
            f"tier_risk={risk_pct:.2%} conf_scale={conf_scale:.2f} "
            f"atr_mult={atr_mult} → stake=${stake} | "
            f"bal=${balance:.2f} conf={confidence:.3f} "
            f"regime={meta.get('regime', '?')}"
        )

        try:
            result = await self.execute_trade_fn(
                symbol=self.SYMBOL,
                action=action,
                amount=stake,
            )

            self.logger.info(
                f"[{self.NAME}] trade placed | action={action} stake={stake} | result={result}"
            )

            signal_log_id = meta.get("signal_log_id")
            if signal_log_id and result and result.get("contract_id"):
                try:
                    async with httpx.AsyncClient() as client:
                        await client.post(
                            f"{BACKEND_URL}/api/signal/mark-executed",
                            json={"signal_id": signal_log_id},
                            timeout=5,
                        )
                except Exception as mark_err:
                    self.logger.warning(f"[{self.NAME}] mark-executed failed: {mark_err}")

            await self.broadcast_fn({
                "type":        "trade_executed",
                "strategy":    self.NAME,
                "action":      action,
                "amount":      stake,
                "symbol":      self.SYMBOL,
                "confidence":  confidence,
                "regime":      meta.get("regime", "UNKNOWN"),
                "contract_id": result.get("contract_id") if result else None,
                "buy_price":   result.get("buy_price")   if result else None,
                "payout":      result.get("payout")      if result else None,
                "meta":        meta,
            })

            return result

        except Exception as e:
            self.logger.error(f"[{self.NAME}] execute_trade_fn failed: {e}")
            await self.broadcast_fn({"type": "trade_error", "strategy": self.NAME, "error": str(e)})
            return None