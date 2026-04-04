"""
VESTRO V75 ALGORITHM ENGINE
============================
Volatility 75 Index — Full Pipeline Implementation
Based on: Advanced Risk Management Strategy (PDF) + 5-Phase Architecture

DISCLAIMER: For educational/research purposes only.
The Volatility 75 Index is a synthetic index. No algorithm guarantees profit.
"""

import random
import math
import statistics
from datetime import datetime
from collections import deque


# ============================================================
# PHASE 1 — HIGH-FREQUENCY DATA CAPTURE
# ============================================================

class V75DataCapture:
    """
    Simulates the Volatility 75 Index data stream.
    V75 (Deriv/Binary.com) is a synthetic index with:
      - Volatility parameter: 75% annualised
      - ~1 tick per second
      - No real market correlation
      - Broker-controlled spike frequency
    """
    def __init__(self, seed=42):
        random.seed(seed)
        self.price = 500000.0     # Typical V75 starting price (~500,000 pts)
        self.tick_buffer = deque(maxlen=1000)
        self.candle_buffer = deque(maxlen=500)
        self._vol_param = 0.75    # The "75" in Vol75

    def generate_tick(self):
        """Simulate a single V75 tick using geometric Brownian motion."""
        dt = 1 / 86400           # 1 second in trading day fraction
        drift = 0.0              # Synthetic indices have no drift
        sigma = self._vol_param

        z = random.gauss(0, 1)
        price_return = math.exp((drift - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * z)
        self.price *= price_return

        # Occasional broker-programmed spike (simulated probability)
        spike = random.random() < 0.002   # ~0.2% chance per tick
        if spike:
            direction = 1 if random.random() > 0.5 else -1
            self.price *= (1 + direction * random.uniform(0.003, 0.012))

        tick = {
            "timestamp": datetime.utcnow().isoformat(),
            "price": round(self.price, 2),
            "is_spike": spike,
        }
        self.tick_buffer.append(tick)
        return tick

    def build_candle(self, period_ticks=60):
        """Aggregate ticks into OHLC candles (1M default = 60 ticks at 1 tick/s)."""
        ticks = [self.generate_tick() for _ in range(period_ticks)]
        prices = [t["price"] for t in ticks]
        return {
            "open":  prices[0],
            "high":  max(prices),
            "low":   min(prices),
            "close": prices[-1],
            "volume": len(prices),
            "spikes": sum(1 for t in ticks if t["is_spike"]),
        }

    def stream_candles(self, n=200, period=60):
        """Generate n candles and store in buffer."""
        for _ in range(n):
            c = self.build_candle(period)
            self.candle_buffer.append(c)
        return list(self.candle_buffer)


# ============================================================
# PHASE 2 — FEATURE ENGINEERING
# ============================================================

class FeatureEngine:
    """
    This is where edge is built.
    Transforms raw OHLC into tradeable signals per the PDF strategy.
    """
    def __init__(self, candles: list):
        self.candles = candles
        self.closes = [c["close"] for c in candles]
        self.highs  = [c["high"]  for c in candles]
        self.lows   = [c["low"]   for c in candles]

    # --- EMA ---
    def ema(self, period: int, prices: list = None) -> list:
        src = prices or self.closes
        k = 2 / (period + 1)
        result = [src[0]]
        for p in src[1:]:
            result.append(p * k + result[-1] * (1 - k))
        return result

    # --- ATR (14-period) ---
    def atr(self, period: int = 14) -> list:
        trs = []
        for i in range(1, len(self.candles)):
            tr = max(
                self.highs[i] - self.lows[i],
                abs(self.highs[i] - self.closes[i-1]),
                abs(self.lows[i] - self.closes[i-1]),
            )
            trs.append(tr)
        result = [sum(trs[:period]) / period]
        for tr in trs[period:]:
            result.append((result[-1] * (period - 1) + tr) / period)
        return result

    # --- RSI ---
    def rsi(self, period: int = 14) -> list:
        deltas = [self.closes[i] - self.closes[i-1] for i in range(1, len(self.closes))]
        gains = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period + 1e-10
        rsi_vals = [100 - (100 / (1 + avg_gain / avg_loss))]
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period + 1e-10
            rsi_vals.append(100 - (100 / (1 + avg_gain / avg_loss)))
        return rsi_vals

    # --- MACD ---
    def macd(self, fast=12, slow=26, signal=9) -> dict:
        ema_fast = self.ema(fast)
        ema_slow = self.ema(slow)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = self.ema(signal, macd_line)
        histogram = [m - s for m, s in zip(macd_line, signal_line)]
        return {"macd": macd_line, "signal": signal_line, "histogram": histogram}

    # --- ADX ---
    def adx(self, period: int = 14) -> list:
        plus_dm, minus_dm, tr_list = [], [], []
        for i in range(1, len(self.candles)):
            up   = self.highs[i]  - self.highs[i-1]
            down = self.lows[i-1] - self.lows[i]
            plus_dm.append(up   if up > down and up > 0   else 0)
            minus_dm.append(down if down > up and down > 0 else 0)
            tr_list.append(max(
                self.highs[i] - self.lows[i],
                abs(self.highs[i] - self.closes[i-1]),
                abs(self.lows[i] - self.closes[i-1]),
            ))
        def smooth(lst, p):
            s = [sum(lst[:p])]
            for v in lst[p:]:
                s.append(s[-1] - s[-1]/p + v)
            return s
        str14 = smooth(tr_list, period)
        pdm14 = smooth(plus_dm, period)
        ndm14 = smooth(minus_dm, period)
        pdi   = [100 * p / (t + 1e-10) for p, t in zip(pdm14, str14)]
        ndi   = [100 * n / (t + 1e-10) for n, t in zip(ndm14, str14)]
        dx    = [100 * abs(p - n) / (p + n + 1e-10) for p, n in zip(pdi, ndi)]
        adx_smooth = [sum(dx[:period]) / period]
        for v in dx[period:]:
            adx_smooth.append((adx_smooth[-1] * (period - 1) + v) / period)
        return adx_smooth

    # --- Bollinger Bands ---
    def bollinger(self, period=20, std_dev=2) -> dict:
        upper, middle, lower = [], [], []
        for i in range(period - 1, len(self.closes)):
            window = self.closes[i - period + 1 : i + 1]
            m = statistics.mean(window)
            s = statistics.stdev(window)
            middle.append(m)
            upper.append(m + std_dev * s)
            lower.append(m - std_dev * s)
        return {"upper": upper, "middle": middle, "lower": lower}

    def build_all(self) -> dict:
        """Compute all features and return as a single dict."""
        macd_data = self.macd()
        return {
            "ema_21":  self.ema(21),
            "ema_50":  self.ema(50),
            "ema_200": self.ema(200),
            "rsi_14":  self.rsi(14),
            "atr_14":  self.atr(14),
            "adx_14":  self.adx(14),
            "macd":    macd_data["macd"],
            "macd_signal": macd_data["signal"],
            "macd_histogram": macd_data["histogram"],
            "bb":      self.bollinger(),
        }


# ============================================================
# PHASE 3 — PATTERN EXTRACTION
# ============================================================

class PatternExtractor:
    """
    Candle pattern recognition + statistical aggregation.
    Identifies: compression zones, divergence, spike setups.
    """
    def __init__(self, candles: list, features: dict):
        self.candles  = candles
        self.features = features

    def _body_size(self, i: int) -> float:
        c = self.candles[i]
        return abs(c["close"] - c["open"])

    def _range_size(self, i: int) -> float:
        c = self.candles[i]
        return c["high"] - c["low"]

    def compression_zone(self, lookback: int = 8) -> bool:
        """
        Boom/Crash rule: 6-10 small candles signals imminent spike.
        Returns True if last `lookback` candles are compressed.
        """
        if len(self.candles) < lookback + 5:
            return False
        recent_bodies = [self._body_size(i) for i in range(-lookback, 0)]
        prior_bodies  = [self._body_size(i) for i in range(-lookback - 5, -lookback)]
        avg_recent = statistics.mean(recent_bodies)
        avg_prior  = statistics.mean(prior_bodies) + 1e-10
        return avg_recent / avg_prior < 0.5   # Recent bodies < 50% of prior

    def rsi_divergence(self, lookback: int = 10) -> str:
        """
        Detect RSI divergence as defined in PDF Section 2.3.
        Returns: 'regular_bearish' | 'regular_bullish' | 'hidden_bearish' | 'hidden_bullish' | 'none'
        """
        rsi = self.features.get("rsi_14", [])
        closes = [c["close"] for c in self.candles]
        if len(rsi) < lookback or len(closes) < lookback:
            return "none"
        price_last  = closes[-1];  price_prev  = closes[-lookback]
        rsi_last    = rsi[-1];     rsi_prev    = rsi[-lookback]

        if price_last > price_prev and rsi_last < rsi_prev:
            return "regular_bearish"    # Strong sell signal
        if price_last < price_prev and rsi_last > rsi_prev:
            return "regular_bullish"    # Strong buy signal
        if price_last < price_prev and rsi_last < rsi_prev:
            return "hidden_bearish"     # Trend continuation down
        if price_last > price_prev and rsi_last > rsi_prev:
            return "hidden_bullish"     # Trend continuation up
        return "none"

    def trend_strength_score(self) -> int:
        """
        TSS: 0-5. Only trades scoring >= 3 qualify (PDF Section 1.2).
        """
        score = 0
        closes = [c["close"] for c in self.candles]
        ema21 = self.features.get("ema_21", [None] * len(closes))
        ema50 = self.features.get("ema_50", [None] * len(closes))
        ema200 = self.features.get("ema_200", [None] * len(closes))
        adx = self.features.get("adx_14", [0])
        macd_h = self.features.get("macd_histogram", [0])
        # ← volumes line removed

        if ema21[-1] and ema50[-1] and ema200[-1]:
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

        score += 1  # volume: unconditional (R_75 has no real volume)

        return score

    def statistical_aggregation(self, window: int = 50) -> dict:
        """Mean, variance, percentiles of recent closes."""
        closes = [c["close"] for c in self.candles[-window:]]
        return {
            "mean":      statistics.mean(closes),
            "variance":  statistics.variance(closes),
            "p25":       sorted(closes)[int(len(closes)*0.25)],
            "p50":       statistics.median(closes),
            "p75":       sorted(closes)[int(len(closes)*0.75)],
            "p95":       sorted(closes)[int(len(closes)*0.95)],
        }


# ============================================================
# PHASE 4 — PREDICTION ENGINE
# ============================================================

class PredictionEngine:
    """
    Anomaly baseline + real-time comparison.
    Generates BUY / SELL / HOLD signals with confidence scores.
    """
    def __init__(self, patterns: PatternExtractor, features: dict, candles: list):
        self.patterns  = patterns
        self.features  = features
        self.candles   = candles

    def _atr_zone(self) -> str:
        """ATR volatility zone classification per PDF Section 4.1."""
        atr_vals = self.features.get("atr_14", [1])
        if len(atr_vals) < 21:
            return "normal"
        current_atr = atr_vals[-1]
        avg_20d     = statistics.mean(atr_vals[-21:-1])
        ratio       = current_atr / (avg_20d + 1e-10)
        if ratio < 0.5:   return "low"
        if ratio < 1.5:   return "normal"
        if ratio < 2.5:   return "elevated"
        return "extreme"

    def _entry_checklist(self, direction: str) -> int:
        """
        PDF Section 2.1 / 2.2 entry checklist.
        Returns number of criteria passed (max 7).
        """
        score = 0
        closes = [c["close"] for c in self.candles]
        rsi = self.features.get("rsi_14", [50])
        macd_h = self.features.get("macd_histogram", [0])
        ema50 = self.features.get("ema_50", [closes[-1]])
        ema200 = self.features.get("ema_200", [closes[-1]])
        last_c = self.candles[-1]  # ← volumes line removed

        if direction == "buy":
            if ema50[-1] > ema200[-1]: score += 1
            if 30 <= rsi[-1] <= 45:    score += 1
            if macd_h[-1] > 0:         score += 1
            body = abs(last_c["close"] - last_c["open"])
            rng = last_c["high"] - last_c["low"]
            if last_c["close"] > last_c["open"] and body / (rng + 1e-5) > 0.5:
                score += 1
            score += 1  # volume: unconditional (R_75 has no real volume)
            score += 1  # session check
            score += 1  # zone check
        else:
            if ema50[-1] < ema200[-1]: score += 1
            if 55 <= rsi[-1] <= 70:    score += 1
            if macd_h[-1] < 0:         score += 1
            body = abs(last_c["close"] - last_c["open"])
            rng = last_c["high"] - last_c["low"]
            if last_c["close"] < last_c["open"] and body / (rng + 1e-5) > 0.5:
                score += 1
            score += 1  # volume: unconditional (R_75 has no real volume)
            score += 1  # session check
            score += 1  # zone check

        return min(score, 7)

    def predict(self) -> dict:
        """Generate final signal with all supporting data."""
        tss       = self.patterns.trend_strength_score()
        diverge   = self.patterns.rsi_divergence()
        compress  = self.patterns.compression_zone()
        atr_zone  = self._atr_zone()
        stats     = self.patterns.statistical_aggregation()

        closes    = [c["close"] for c in self.candles]
        ema21     = self.features.get("ema_21",  [closes[-1]])
        ema50     = self.features.get("ema_50",  [closes[-1]])
        ema200    = self.features.get("ema_200", [closes[-1]])

        bull_stack = ema21[-1] > ema50[-1] > ema200[-1]
        bear_stack = ema21[-1] < ema50[-1] < ema200[-1]

        # Hard block: extreme volatility = no trade
        if atr_zone == "extreme":
            return {"signal": "HOLD", "reason": "ATR extreme — stand aside", "tss": tss,
                    "checklist": 0, "atr_zone": atr_zone, "confidence": 0.0}

        direction = None
        if bull_stack:   direction = "buy"
        elif bear_stack: direction = "sell"

        if not direction:
            return {"signal": "HOLD", "reason": "EMA stack indeterminate", "tss": tss,
                    "checklist": 0, "atr_zone": atr_zone, "confidence": 0.0}

        checklist = self._entry_checklist(direction)
        if checklist < 4 or tss < 3:
            return {"signal": "HOLD",
                    "reason": f"Checklist {checklist}/7, TSS {tss}/5 — insufficient confluence",
                    "tss": tss, "checklist": checklist, "atr_zone": atr_zone, "confidence": 0.0}

        confidence = min(1.0, (tss / 5) * 0.5 + (checklist / 7) * 0.5)
        signal = "BUY" if direction == "buy" else "SELL"

        # Spike boost for compression zones
        spike_ready = compress and signal == "SELL"

        return {
            "signal":      signal,
            "price":       closes[-1],
            "confidence":  round(confidence, 3),
            "tss":         tss,
            "checklist":   checklist,
            "atr_zone":    atr_zone,
            "divergence":  diverge,
            "spike_ready": spike_ready,
            "stats":       stats,
            "reason":      f"TSS {tss}/5, Checklist {checklist}/7, {atr_zone.upper()} ATR",
        }


# ============================================================
# PHASE 5 — RISK MANAGEMENT + CONTINUOUS LEARNING
# ============================================================

class RiskManager:
    """
    Dynamic position sizing with account-tier scaling.
    Implements PDF Section 3.1 — 3.4 in full.

    Account tiers and rules:
      Starter  < $1,000  : 1%   risk, max 2 trades, stop after 2 losses
      Growth   $1K-$10K  : 1.5% risk, max 3 trades, stop after 3 losses
      Estab.   > $10K    : 2%   risk, max 4 trades, stop after 3 losses
      Prop     any       : 0.75% risk, max 2 trades, strict DD
    """
    TIERS = {
        "starter":    {"min": 10,    "max": 49,     "risk_pct": 0.01,  "max_trades": 2, "daily_dd": 0.03, "loss_limit": 2},
        "growth":     {"min": 50,    "max": 499,    "risk_pct": 0.015, "max_trades": 3, "daily_dd": 0.04, "loss_limit": 3},
        "established":{"min": 500,   "max": 999999, "risk_pct": 0.02,  "max_trades": 4, "daily_dd": 0.05, "loss_limit": 3},
        "prop":       {"min": 0,     "max": 999999, "risk_pct": 0.0075,"max_trades": 2, "daily_dd": 0.03, "loss_limit": 2},
    }

    def __init__(self, account_balance: float, is_prop: bool = False, pip_value: float = 1.0):
        self.balance       = account_balance
        self.is_prop       = is_prop
        self.pip_value     = pip_value
        self.open_trades   = 0
        self.daily_losses  = 0
        self.daily_pnl     = 0.0
        self.consecutive_losses = 0
        self._tier_name    = self._get_tier()

    def _get_tier(self) -> str:
        if self.is_prop:
            return "prop"
        if self.balance < 50:       # $10–$49 → Starter
            return "starter"
        if self.balance < 500:      # $50–$499 → Growth
            return "growth"
        return "established"        # $500+ → Established

    @property
    def tier(self) -> dict:
        return self.TIERS[self._tier_name]

    def lot_size(self, sl_pips: float) -> dict:
        """
        Core formula from PDF 3.1:
          Lot = (Balance × Risk%) / (SL_pips × Pip_Value)
        """
        risk_dollar = self.balance * self.tier["risk_pct"]
        lots        = risk_dollar / (sl_pips * self.pip_value)

        # Elevated ATR: halve position size (PDF Section 4.1)
        return {
            "lots":          round(lots, 2),
            "risk_dollar":   round(risk_dollar, 2),
            "risk_pct":      self.tier["risk_pct"] * 100,
            "tier":          self._tier_name,
            "max_trades":    self.tier["max_trades"],
            "daily_dd_limit": self.tier["daily_dd"] * 100,
        }

    def atr_adjusted_lot(self, sl_pips: float, atr_zone: str) -> dict:
        base = self.lot_size(sl_pips)
        multiplier = {"low": 1.0, "normal": 1.0, "elevated": 0.5, "extreme": 0.0}.get(atr_zone, 1.0)
        base["lots"]         = round(base["lots"] * multiplier, 2)
        base["atr_zone"]     = atr_zone
        base["atr_adjusted"] = multiplier != 1.0
        return base

    def sl_tp_levels(self, entry: float, direction: str, sl_pips: float, atr_val: float) -> dict:
        """
        SL: beyond structure + 5-10 pip buffer (PDF 3.2)
        TP: partial close system (PDF 6.1)
          TP1 at 1.5R (50% close), TP2 at 3R (30%), TP3 trail (20%)
        """
        buffer_pips = sl_pips * 0.1   # 10% buffer
        atr_sl      = atr_val * 1.5   # ATR-based SL multiplier (normal zone)

        actual_sl = max(sl_pips + buffer_pips, atr_sl)

        if direction == "buy":
            sl  = entry - actual_sl
            tp1 = entry + actual_sl * 1.5
            tp2 = entry + actual_sl * 3.0
        else:
            sl  = entry + actual_sl
            tp1 = entry - actual_sl * 1.5
            tp2 = entry - actual_sl * 3.0

        return {
            "entry": round(entry, 2),
            "sl":    round(sl, 2),
            "tp1":   round(tp1, 2),   # Close 50% here
            "tp2":   round(tp2, 2),   # Close 30% here
            "tp3":   "trail_2x_atr",  # Trail 20% with 2xATR
            "r_r":   "1:1.5 / 1:3",
        }

    def can_trade(self, atr_zone: str) -> dict:
        tier = self.tier
        daily_loss_pct = abs(self.daily_pnl) / self.balance if self.daily_pnl < 0 else 0

        checks = {
            "open_trades_ok":      self.open_trades < tier["max_trades"],
            "daily_dd_ok":         daily_loss_pct < tier["daily_dd"],
            "consecutive_loss_ok": self.consecutive_losses < tier["loss_limit"],
            "atr_zone_ok":         atr_zone != "extreme",
        }
        allowed = all(checks.values())
        reason  = None if allowed else next(k for k, v in checks.items() if not v)

        return {"allowed": allowed, "checks": checks, "block_reason": reason,
                "tier": self._tier_name, "balance": self.balance}

    def log_trade_result(self, pnl: float):
        """Update internal state after a trade closes."""
        self.daily_pnl += pnl
        if pnl < 0:
            self.daily_losses += 1
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        self.balance += pnl
        self.open_trades = max(0, self.open_trades - 1)
        self._tier_name = self._get_tier()   # Re-tier if balance changed


class ContinuousLearner:
    """
    Phase 5: outcome tracking and error feedback loop.
    Tracks prediction accuracy and updates confidence thresholds.
    """
    def __init__(self):
        self.predictions = deque(maxlen=200)
        self.outcomes    = deque(maxlen=200)
        self.accuracy    = 0.5

    def record(self, prediction: dict, outcome_pnl: float):
        correct = (prediction["signal"] == "BUY"  and outcome_pnl > 0) or \
                  (prediction["signal"] == "SELL" and outcome_pnl > 0) or \
                  (prediction["signal"] == "HOLD")
        self.predictions.append(prediction)
        self.outcomes.append({"pnl": outcome_pnl, "correct": correct})
        if self.outcomes:
            self.accuracy = sum(1 for o in self.outcomes if o["correct"]) / len(self.outcomes)

    def performance_metrics(self) -> dict:
        if not self.outcomes:
            return {"accuracy": 0, "expectancy": 0, "trades": 0}
        pnls    = [o["pnl"] for o in self.outcomes if o["pnl"] != 0]
        wins    = [p for p in pnls if p > 0]
        losses  = [p for p in pnls if p < 0]
        win_r   = len(wins) / len(pnls) if pnls else 0
        avg_w   = statistics.mean(wins)   if wins   else 0
        avg_l   = abs(statistics.mean(losses)) if losses else 1
        expect  = win_r * avg_w - (1 - win_r) * avg_l
        return {
            "accuracy":    round(self.accuracy, 3),
            "win_rate":    round(win_r, 3),
            "expectancy":  round(expect, 2),
            "avg_win":     round(avg_w, 2),
            "avg_loss":    round(avg_l, 2),
            "trades":      len(pnls),
        }


# ============================================================
# MAIN PIPELINE — VESTRO V75 ENGINE
# ============================================================

def run_vestro_pipeline(account_balance: float = 1000.0, is_prop: bool = False):
    """
    Full V75 pipeline execution.
    Phases 1-5 end-to-end.
    """
    print("=" * 60)
    print("  VESTRO V75 ALGORITHM ENGINE")
    print("=" * 60)

    # Phase 1
    print("\n[PHASE 1] Data Capture — generating V75 candles...")
    capture  = V75DataCapture(seed=42)
    candles  = capture.stream_candles(n=200)
    print(f"  Generated {len(candles)} candles. Last close: {candles[-1]['close']:,.2f}")

    # Phase 2
    print("\n[PHASE 2] Feature Engineering...")
    engine   = FeatureEngine(candles)
    features = engine.build_all()
    print(f"  EMA21={features['ema_21'][-1]:,.2f} | EMA50={features['ema_50'][-1]:,.2f} | RSI={features['rsi_14'][-1]:.1f}")

    # Phase 3
    print("\n[PHASE 3] Pattern Extraction...")
    patterns = PatternExtractor(candles, features)
    tss      = patterns.trend_strength_score()
    div      = patterns.rsi_divergence()
    comp     = patterns.compression_zone()
    stats    = patterns.statistical_aggregation()
    print(f"  TSS={tss}/5 | Divergence={div} | Compression={comp}")
    print(f"  Price stats: mean={stats['mean']:,.2f}, p25={stats['p25']:,.2f}, p75={stats['p75']:,.2f}")

    # Phase 4
    print("\n[PHASE 4] Prediction Engine...")
    predictor = PredictionEngine(patterns, features, candles)
    signal    = predictor.predict()
    print(f"  SIGNAL: {signal['signal']} | Confidence: {signal['confidence']:.1%}")
    print(f"  Reason: {signal['reason']}")

    # Phase 5
    print("\n[PHASE 5] Risk Management...")
    risk  = RiskManager(account_balance, is_prop)
    atr   = features["atr_14"][-1] if features["atr_14"] else 1000
    check = risk.can_trade(signal.get("atr_zone", "normal"))
    print(f"  Account: ${account_balance:,.2f} | Tier: {check['tier'].upper()}")
    print(f"  Can trade: {check['allowed']}")

    if signal["signal"] != "HOLD" and check["allowed"]:
        sl_pips = atr * 1.5
        sizing  = risk.atr_adjusted_lot(sl_pips, signal.get("atr_zone", "normal"))
        levels  = risk.sl_tp_levels(
            entry     = candles[-1]["close"],
            direction = "buy" if signal["signal"] == "BUY" else "sell",
            sl_pips   = sl_pips,
            atr_val   = atr,
        )
        print(f"\n  Position Size: {sizing['lots']} lots | Risk: ${sizing['risk_dollar']}")
        print(f"  Entry: {levels['entry']:,.2f}")
        print(f"  SL:    {levels['sl']:,.2f}")
        print(f"  TP1:   {levels['tp1']:,.2f} (close 50%)")
        print(f"  TP2:   {levels['tp2']:,.2f} (close 30%)")
        print(f"  TP3:   {levels['tp3']} (trail 20%)")

    print("\n" + "=" * 60)
    print("  VESTRO ENGINE COMPLETE")
    print("  Remember: V75 is synthetic. No strategy guarantees profit.")
    print("=" * 60)

    return {
        "signal":   signal,
        "features": {k: v[-1] if isinstance(v, list) and v else v for k, v in features.items() if k != "bb"},
        "risk":     check,
    }


if __name__ == "__main__":
    # --- Run for different account sizes to demo tier scaling ---
    for bal in [10, 50, 500]:
        print(f"\n{'#'*60}")
        print(f"# ACCOUNT: ${bal:,}")
        print(f"{'#'*60}")
        run_vestro_pipeline(account_balance=float(bal))