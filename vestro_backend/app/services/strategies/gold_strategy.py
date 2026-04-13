"""
gold_strategy.py
================
VESTRO Gold Strategy — XAU/USD (frxXAUUSD on Deriv)

How gold differs from V75 and why this strategy accounts for it
---------------------------------------------------------------

1. MARKET HOURS GUARD
   Gold trades 23/5 — closed Friday 22:00 UTC to Sunday 22:00 UTC.
   Also illiquid at daily open (Sunday 22:00–22:30 UTC) and
   around the NY/London close (21:00–22:00 UTC Friday).
   Strategy hard-blocks outside trading hours and during thin open windows.

2. SLOWER CANDLE GRANULARITY
   V75 uses 1-minute candles — synthetics never sleep and tick constantly.
   Gold uses 5-minute candles (granularity=300). Gold moves in bigger,
   slower waves driven by macro flow, not pure tick noise.
   300 candles × 5min = 25 hours of context, enough to see daily structure.

3. SESSION AWARENESS
   Gold has three distinct liquidity windows:
     LONDON  08:00–12:00 UTC  — European open, strong directional moves
     NEW YORK 13:00–17:00 UTC  — US session overlap, highest volume
     ASIAN   00:00–06:00 UTC  — thin, choppy, avoid trading
   Strategy only fires in London and New York sessions.
   Asian session → HOLD regardless of signal.

4. WIDER ATR-BASED SL/TP
   Gold ATR on 5m candles is much larger in absolute terms than V75.
   SL multiplier raised to 2.0× ATR (vs 1.5× on V75).
   TP multiplier raised to 2.5× ATR (gold trends longer than synthetics).

5. RSI THRESHOLDS ADJUSTED
   Gold mean-reverts less aggressively than synthetics.
   RSI buy threshold raised to 60 (was 55 on V75).
   RSI sell threshold lowered to 40 (was 45 on V75).
   This filters out choppy mid-RSI noise that is common in gold.

6. REGIME GATES ADAPTED FOR GOLD
   CRASH    → suppress SELL (gold is a safe-haven, CRASH = BUY pressure on gold)
   HIGH_VOL → suppress both directions (gold spikes are mean-reverting, dangerous)
   RANGE    → tighten checklist as normal
   TREND    → normal pipeline
   UNKNOWN  → fail-open

7. LOWER STAKE SIZING
   Gold is a real-market instrument — more slippage, wider spreads.
   Risk percent reduced to 0.75% of balance (vs 1–2% on V75).
   Max stake capped at $5 (vs $8 on V75).

8. CONFIDENCE FLOOR RAISED
   Min confidence for execution raised to 0.65 (vs 0.60 on V75).
   Gold signals are noisier — we want only high-conviction entries.

To activate:
    1. Place this file in vestro_backend/app/services/strategies/
    2. In __init__.py add:
         from .gold_strategy import GoldStrategy
         STRATEGY_REGISTRY = [..., GoldStrategy]
    3. In walk_forward_validator.py add:
         "frxXAUUSD": "Gold"
    4. Deploy — the runner picks it up automatically.
"""

import httpx
import json
import os
import statistics
import websockets
from datetime import datetime, timezone

from .base_strategy import BaseStrategy
from app.services.regime_cache import get_current_regime

DERIV_APP_ID = os.environ["DERIV_APP_ID"]
BACKEND_URL  = os.environ.get("BACKEND_URL", "https://vestro-jpg.onrender.com")

# Gold trades 23/5 — these are UTC hours
_MARKET_OPEN_HOUR  = 22   # Sunday open
_MARKET_CLOSE_DAY  = 4    # Friday (weekday index: Mon=0, Fri=4)
_MARKET_CLOSE_HOUR = 22   # Friday close

# Active session windows (UTC hour ranges, inclusive)
_LONDON_OPEN  = (8,  12)
_NY_OPEN      = (13, 17)


def _is_market_open() -> bool:
    """Returns True if gold market is currently open."""
    now = datetime.now(timezone.utc)
    wd  = now.weekday()   # Mon=0 ... Sun=6
    h   = now.hour

    # Weekend: Sat all day, Sun before 22:00
    if wd == 5:
        return False
    if wd == 6 and h < 22:
        return False
    # Friday after 22:00 UTC
    if wd == _MARKET_CLOSE_DAY and h >= _MARKET_CLOSE_HOUR:
        return False

    return True


def _active_session() -> str | None:
    """
    Returns the current liquidity session name or None if outside sessions.
    Only London and NY sessions are traded — Asian session is skipped.
    """
    h = datetime.now(timezone.utc).hour
    if _LONDON_OPEN[0] <= h <= _LONDON_OPEN[1]:
        return "LONDON"
    if _NY_OPEN[0] <= h <= _NY_OPEN[1]:
        return "NEW_YORK"
    return None   # Asian or off-hours — skip


# ── Feature engine (identical to V75, reused) ─────────────────────────────────

class _FeatureEngine:
    def __init__(self, candles):
        self.candles = candles
        self.closes  = [c["close"] for c in candles]
        self.highs   = [c["high"]  for c in candles]
        self.lows    = [c["low"]   for c in candles]

    def ema(self, period, prices=None):
        src = prices or self.closes
        k   = 2 / (period + 1)
        r   = [src[0]]
        for p in src[1:]:
            r.append(p * k + r[-1] * (1 - k))
        return r

    def atr(self, period=14):
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
        r = [sum(trs[:period]) / period]
        for tr in trs[period:]:
            r.append((r[-1] * (period - 1) + tr) / period)
        return r

    def rsi(self, period=14):
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

    def macd(self, fast=12, slow=26, signal=9):
        ef   = self.ema(fast)
        es   = self.ema(slow)
        line = [f - s for f, s in zip(ef, es)]
        sig  = self.ema(signal, line)
        hist = [m - s for m, s in zip(line, sig)]
        return {"macd": line, "signal": sig, "histogram": hist}

    def adx(self, period=14):
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

    def build_all(self):
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


# ── Prediction engine (gold-adjusted thresholds) ──────────────────────────────

class _GoldPredictionEngine:
    """
    Same structure as V75's prediction engine but with gold-specific defaults:
    - RSI buy max: 60  (V75: 55) — gold trends through RSI levels that would
      signal overbought on synthetics
    - RSI sell min: 40 (V75: 45) — same reason on the downside
    - Checklist min: 4 (V75: 3)  — gold is noisier, require more confluence
    - ADX min: 22     (V75: 25)  — gold trends at lower ADX readings
    """

    # Gold-specific defaults (override via calibration_loader if available)
    RSI_BUY_MAX    = 60
    RSI_SELL_MIN   = 40
    CHECKLIST_MIN  = 4
    ADX_MIN        = 22
    BODY_RATIO_MIN = 0.35   # gold candles have smaller bodies — slightly relaxed

    def __init__(self, features, candles, thresholds=None):
        self.features   = features
        self.candles    = candles
        self.t          = thresholds

    def _get(self, attr, default):
        """Read from calibration thresholds, fall back to gold default."""
        if self.t and hasattr(self.t, attr):
            v = getattr(self.t, attr)
            if v is not None:
                return v
        return default

    def _atr_zone(self):
        atr_vals = self.features.get("atr_14", [1])
        if len(atr_vals) < 21:
            return "normal"
        ratio = atr_vals[-1] / (statistics.mean(atr_vals[-21:-1]) + 1e-10)
        if ratio < 0.5:  return "low"
        if ratio < 1.5:  return "normal"
        if ratio < 2.0:  return "elevated"   # tighter for gold — 2.0 vs 2.5
        return "extreme"

    def _entry_checklist(self, direction):
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

        rsi_buy_max    = self._get("rsi_buy_max",    self.RSI_BUY_MAX)
        rsi_sell_min   = self._get("rsi_sell_min",   self.RSI_SELL_MIN)
        body_ratio_min = self._get("body_ratio_min", self.BODY_RATIO_MIN)

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
            if volumes[-1] > avg_v * 1.1:
                score += 1

        score += 1   # session already verified before predict() is called
        score += 1   # structure check placeholder
        return min(score, 7)

    def tss(self):
        score  = 0
        closes = [c["close"] for c in self.candles]
        ema21  = self.features.get("ema_21",  [closes[-1]])
        ema50  = self.features.get("ema_50",  [closes[-1]])
        ema200 = self.features.get("ema_200", [closes[-1]])
        adx    = self.features.get("adx_14",  [0])
        macd_h = self.features.get("macd_histogram", [0])

        bull = ema21[-1] > ema50[-1] > ema200[-1]
        bear = ema21[-1] < ema50[-1] < ema200[-1]
        if bull or bear:       score += 1
        if adx and adx[-1] > self._get("adx_min", self.ADX_MIN): score += 1
        if closes[-1] > ema200[-1]:                               score += 1
        if macd_h and macd_h[-1] > 0:                            score += 1
        if len(self.candles) > 1:
            vols = [c["volume"] for c in self.candles]
            if len(vols) > 1 and vols[-1] > statistics.mean(vols[:-1]) * 1.2:
                score += 1
        return score

    def predict(self, effective_checklist_min=None):
        tss_val  = self.tss()
        atr_zone = self._atr_zone()

        if atr_zone == "extreme":
            return {
                "signal": "HOLD", "reason": "ATR extreme — gold spike, skip",
                "tss": tss_val, "checklist": 0, "atr_zone": atr_zone, "confidence": 0.0,
            }

        closes  = [c["close"] for c in self.candles]
        ema21   = self.features.get("ema_21",  [closes[-1]])
        ema50   = self.features.get("ema_50",  [closes[-1]])
        ema200  = self.features.get("ema_200", [closes[-1]])

        bull_pts = sum([ema21[-1] > ema50[-1], ema50[-1] > ema200[-1], ema21[-1] > ema200[-1]])
        bear_pts = sum([ema21[-1] < ema50[-1], ema50[-1] < ema200[-1], ema21[-1] < ema200[-1]])

        if bull_pts >= 2:
            direction = "buy"
        elif bear_pts >= 2:
            direction = "sell"
        else:
            return {
                "signal": "HOLD", "reason": "EMA stack indeterminate",
                "tss": tss_val, "checklist": 0, "atr_zone": atr_zone, "confidence": 0.0,
            }

        checklist    = self._entry_checklist(direction)
        min_checklist = effective_checklist_min if effective_checklist_min is not None \
                        else self._get("checklist_min", self.CHECKLIST_MIN)
        min_tss       = self._get("tss_min", 2)

        if checklist < min_checklist or tss_val < min_tss:
            return {
                "signal": "HOLD",
                "reason": f"Checklist {checklist}/{min_checklist}, TSS {tss_val}/{min_tss}",
                "tss": tss_val, "checklist": checklist,
                "atr_zone": atr_zone, "confidence": 0.0,
            }

        confidence = min(1.0, (tss_val / 5) * 0.5 + (checklist / 7) * 0.5)
        min_conf   = self._get("confidence_min", 0.65)   # ← gold floor is 0.65 not 0.60

        if confidence < min_conf:
            return {
                "signal": "HOLD",
                "reason": f"confidence {confidence:.2f} < floor {min_conf}",
                "tss": tss_val, "checklist": checklist,
                "atr_zone": atr_zone, "confidence": confidence,
            }

        return {
            "signal":     "BUY" if direction == "buy" else "SELL",
            "price":      closes[-1],
            "confidence": round(confidence, 3),
            "tss":        tss_val,
            "checklist":  checklist,
            "atr_zone":   atr_zone,
            "reason":     f"TSS {tss_val}/5, Checklist {checklist}/7, {atr_zone.upper()} ATR",
        }


# ── Gold Strategy ─────────────────────────────────────────────────────────────

class GoldStrategy(BaseStrategy):
    NAME   = "Gold"
    SYMBOL = "frxXAUUSD"

    # 5-min candles, wider SL/TP for gold
    GRANULARITY = 300    # 5 minutes
    CANDLE_COUNT = 300   # 300 × 5min = 25h of context
    SL_ATR_MULT  = 2.0   # wider SL than V75's 1.5
    TP_ATR_MULT  = 2.5   # longer TP — gold trends
    MAX_STAKE    = 5.0   # lower than V75's $8
    RISK_PCT     = 0.0075  # 0.75% — conservative for real-market instrument
    EXEC_CONF_MIN = 0.65   # higher bar than V75's 0.60

    def __init__(self, api_token, broadcast_fn, execute_trade_fn,
                 balance=1000.0, is_prop=False):
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
                "granularity":   self.GRANULARITY,
                "count":         self.CANDLE_COUNT,
                "end":           "latest",
            }))
            data = json.loads(await ws.recv())

        candles = [
            {
                "open":   float(c["open"]),
                "high":   float(c["high"]),
                "low":    float(c["low"]),
                "close":  float(c["close"]),
                "volume": self.GRANULARITY,
                "epoch":  c.get("epoch", 0),
            }
            for c in data.get("candles", [])
        ]
        self.logger.info(
            f"[{self.NAME}] fetched {len(candles)} candles | "
            f"balance={self.balance} | last={candles[-1]['close'] if candles else 'N/A'}"
        )
        return {"candles": candles}

    async def compute_signal(self, market_data: dict) -> dict:
        candles = market_data["candles"]

        # ── Market hours guard ────────────────────────────────────────────
        if not _is_market_open():
            return {
                "signal": "HOLD", "symbol": self.SYMBOL,
                "confidence": 0.0, "reason": "Market closed (weekend)",
                "amount": 0.0, "meta": {}, "indicators": {},
            }

        session = _active_session()
        if session is None:
            return {
                "signal": "HOLD", "symbol": self.SYMBOL,
                "confidence": 0.0,
                "reason": "Outside London/NY session — Asian session skipped",
                "amount": 0.0, "meta": {}, "indicators": {},
            }

        if len(candles) < 220:
            return {
                "signal": "HOLD", "symbol": self.SYMBOL,
                "confidence": 0.0,
                "reason": f"Insufficient candles ({len(candles)}/220)",
                "amount": 0.0, "meta": {}, "indicators": {},
            }

        from ml.calibration_loader import get_thresholds
        t        = get_thresholds(self.SYMBOL)
        regime   = get_current_regime(self.SYMBOL)
        features = _FeatureEngine(candles).build_all()
        engine   = _GoldPredictionEngine(features, candles, t)

        effective_chk = t.effective_checklist_min() if t and hasattr(t, "effective_checklist_min") \
                        else _GoldPredictionEngine.CHECKLIST_MIN
        result = engine.predict(effective_checklist_min=effective_chk)

        # ── Gold-specific regime gates ────────────────────────────────────
        if result["signal"] != "HOLD":
            sig = result["signal"]

            if regime == "CRASH" and sig == "SELL":
                # Gold is a safe-haven — crash events drive gold UP not down
                result = {
                    "signal": "HOLD",
                    "reason": "CRASH regime — SELL suppressed (gold safe-haven bid)",
                    "tss": result.get("tss", 0), "checklist": result.get("checklist", 0),
                    "atr_zone": result.get("atr_zone", "normal"), "confidence": 0.0,
                }

            elif regime == "HIGH_VOL":
                # Gold spikes are violent and mean-reverting — skip both directions
                result = {
                    "signal": "HOLD",
                    "reason": "HIGH_VOL regime — gold spike risk, both directions suppressed",
                    "tss": result.get("tss", 0), "checklist": result.get("checklist", 0),
                    "atr_zone": result.get("atr_zone", "normal"), "confidence": 0.0,
                }

        atr_val    = features["atr_14"][-1] if features.get("atr_14") else 1.0
        atr_zone   = result.get("atr_zone", "normal")
        tss        = result.get("tss", 0)
        confidence = result.get("confidence", 0.0)
        entry      = candles[-1]["close"]

        # SL/TP — wider multipliers for gold
        if result["signal"] == "BUY":
            sl = round(entry - atr_val * self.SL_ATR_MULT, 4)
            tp = round(entry + atr_val * self.TP_ATR_MULT, 4)
        else:
            sl = round(entry + atr_val * self.SL_ATR_MULT, 4)
            tp = round(entry - atr_val * self.TP_ATR_MULT, 4)

        # Stake sizing — conservative for gold
        atr_mult   = {"low": 1.0, "normal": 1.0, "elevated": 0.5, "extreme": 0.0}.get(atr_zone, 1.0)
        conf_scale = max(0.5, min(1.5, 1.0 + (confidence - 0.65) * 2.5))
        stake      = round(max(0.35, min(self.MAX_STAKE, self.balance * self.RISK_PCT * conf_scale * atr_mult)), 2)

        indicators = {
            "rsi":       round(features["rsi_14"][-1], 2)         if features.get("rsi_14")         else None,
            "adx":       round(features["adx_14"][-1], 2)         if features.get("adx_14")         else None,
            "atr":       round(atr_val, 4),
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
                "regime":     regime,
                "session":    session,
                "reason":     result.get("reason", ""),
            }
        })

        # ── Write SignalLog ───────────────────────────────────────────────
        signal_log_id = None
        if result["signal"] != "HOLD":
            from app.database import AsyncSessionLocal
            from ml.signal_log_model import SignalLog
            try:
                dirval = 1 if result["signal"] == "BUY" else -1
                row = SignalLog(
                    strategy    = self.NAME,
                    symbol      = self.SYMBOL,
                    signal      = result["signal"],
                    direction   = dirval,
                    entry_price = entry,
                    tp_price    = tp,
                    sl_price    = sl,
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
                    captured_at = datetime.utcnow(),
                )
                async with AsyncSessionLocal() as db:
                    db.add(row)
                    await db.commit()
                    await db.refresh(row)
                    signal_log_id = row.id
            except Exception as e:
                self.logger.warning(f"[{self.NAME}] SignalLog insert failed: {e}")

        return {
            "signal":     result["signal"],
            "symbol":     self.SYMBOL,
            "confidence": confidence,
            "reason":     result.get("reason", ""),
            "amount":     stake if result["signal"] != "HOLD" else 0.0,
            "indicators": indicators,
            "meta": {
                "signal_log_id": signal_log_id,
                "tss":           tss,
                "checklist":     result.get("checklist"),
                "atr_zone":      atr_zone,
                "atr_val":       round(atr_val, 4),
                "sl":            sl,
                "tp":            tp,
                "entry":         entry,
                "balance":       self.balance,
                "regime":        regime,
                "session":       session,
            },
        }

    async def should_execute(self, signal: dict) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                status = await client.get(f"{BACKEND_URL}/api/bot/status", timeout=5)
                bot_running = status.json().get("running", False)

            if not bot_running:
                return False
            if not _is_market_open():
                return False
            if _active_session() is None:
                return False
            if signal.get("meta", {}).get("atr_zone") == "extreme":
                return False
            if signal.get("amount", 0) <= 0:
                return False

            confidence = signal.get("confidence", 0.0)
            if confidence < self.EXEC_CONF_MIN:
                self.logger.info(
                    f"[{self.NAME}] confidence {confidence:.3f} < {self.EXEC_CONF_MIN} — skip"
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
        confidence = signal.get("confidence", 0.65)
        meta       = signal.get("meta", {})
        balance    = meta.get("balance", self.balance) or self.balance
        atr_zone   = meta.get("atr_zone", "normal")

        atr_mult   = {"low": 1.0, "normal": 1.0, "elevated": 0.5, "extreme": 0.0}.get(atr_zone, 1.0)
        if atr_mult == 0.0:
            return None

        conf_scale = max(0.5, min(1.5, 1.0 + (confidence - 0.65) * 2.5))
        stake      = round(max(0.35, min(self.MAX_STAKE, balance * self.RISK_PCT * conf_scale * atr_mult)), 2)

        self.logger.info(
            f"[{self.NAME}] EXECUTING {action.upper()} | "
            f"stake=${stake} bal=${balance:.2f} conf={confidence:.3f} "
            f"session={meta.get('session')} regime={meta.get('regime')}"
        )

        try:
            result = await self.execute_trade_fn(
                symbol=self.SYMBOL,
                action=action,
                amount=stake,
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
                except Exception as e:
                    self.logger.warning(f"[{self.NAME}] mark-executed failed: {e}")

            await self.broadcast_fn({
                "type":        "trade_executed",
                "strategy":    self.NAME,
                "action":      action,
                "amount":      stake,
                "symbol":      self.SYMBOL,
                "confidence":  confidence,
                "session":     meta.get("session"),
                "regime":      meta.get("regime", "UNKNOWN"),
                "contract_id": result.get("contract_id") if result else None,
                "buy_price":   result.get("buy_price")   if result else None,
                "payout":      result.get("payout")      if result else None,
            })

            return result

        except Exception as e:
            self.logger.error(f"[{self.NAME}] execute failed: {e}")
            await self.broadcast_fn({"type": "trade_error", "strategy": self.NAME, "error": str(e)})
            return None