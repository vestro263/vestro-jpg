"""
crash500_strategy.py
====================
Crash 500 Index — Spike Rebound Scalper
Wraps the Crash500 logic into BaseStrategy so strategy_runner
can call it the same way as V75.

Entry checklist (ALL must pass):
  [1] SPIKE DEPTH     >= 3.0pts drop within last 8s
  [2] SPIKE RECOVERY  price bounced >= 0.5pts off spike low
  [3] TREND ALIGNED   price now > price 30s ago
  [4] MOMENTUM OK     last 5 ticks net positive
"""

import httpx
import json
import os
import time
import websockets
from collections import deque
from ml.signal_logger import log_signal, mark_executed
from .base_strategy import BaseStrategy

DERIV_APP_ID = os.environ["DERIV_APP_ID"]
BACKEND_URL  = os.environ.get("BACKEND_URL", "https://vestro-jpg.onrender.com")

# ── Risk params ───────────────────────────────────────────────
SL_POINTS   = 60.0
TP_POINTS   = 90.0
RISK_PCT    = 0.01

# ── Entry quality filters ─────────────────────────────────────
MIN_SPIKE_PTS   = 3.0    # minimum drop to qualify as a spike
RECOVERY_PTS    = 0.5    # bounce required off spike low
SPIKE_WINDOW_S  = 8.0    # seconds to look back for spike
TREND_WINDOW_S  = 30.0   # seconds for broad trend check
MICRO_TICKS     = 5      # last N ticks must be net positive

# ── Session discipline ────────────────────────────────────────
MAX_TRADES    = 3
COOLDOWN_SEC  = 300      # 5 min between trades


class Crash500Strategy(BaseStrategy):
    NAME   = "Crash500"
    SYMBOL = "CRASH500"

    def __init__(self, api_token, broadcast_fn, execute_trade_fn,
                 balance: float = 1000.0):
        super().__init__(api_token, broadcast_fn, execute_trade_fn)
        self.balance         = balance
        self._price_history  = deque(maxlen=3000)   # ~10 min at 5Hz
        self._spike_low      = None
        self._spike_low_ts   = 0.0
        self._trade_count    = 0
        self._last_trade_ts  = 0.0

    # ── Phase 1 — fetch latest ticks via WebSocket ────────────
    async def fetch_market_data(self) -> dict:
        url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"authorize": self.api_token}))
            await ws.recv()
            await ws.send(json.dumps({
                "ticks_history": "CRASH500",
                "count": 150,
                "end": "latest"
            }))
            data = json.loads(await ws.recv())

        prices     = data["history"]["prices"]
        timestamps = data["history"]["times"]
        now        = time.time()

        # Rebuild price history from fetched ticks
        for ts, price in zip(timestamps, prices):
            self._price_history.append((float(ts), float(price)))

        latest_bid = float(prices[-1])
        return {
            "prices":    prices,
            "times":     timestamps,
            "latest":    latest_bid,
            "now":       now,
        }

    # ── Phase 2 — run the 4-check entry checklist ─────────────
    async def compute_signal(self, market_data: dict) -> dict:
        from ..ml.calibration_loader import get_thresholds
        t = get_thresholds(self.SYMBOL)

        now = market_data["now"]
        bid = market_data["latest"]
        hist = list(self._price_history)

        if len(hist) < 30:
            return self._hold("warming up — not enough ticks", bid)

        prices = [p for _, p in hist]

        # ── 🔥 CALIBRATED THRESHOLDS ──
        spike_threshold = getattr(t, "spike_min", None) or MIN_SPIKE_PTS
        recovery_threshold = getattr(t, "recovery_min", None) or RECOVERY_PTS

        # [1] Spike depth
        cutoff_spike = now - SPIKE_WINDOW_S
        recent_prices = [p for t_, p in hist if t_ >= cutoff_spike]
        high_spike = max(recent_prices) if recent_prices else bid
        drop_spike = high_spike - bid

        spike_ok = drop_spike >= spike_threshold

        # Track spike low
        if spike_ok:
            if self._spike_low is None or bid < self._spike_low:
                self._spike_low = bid
                self._spike_low_ts = now

        # Expire stale spike low (45s)
        if self._spike_low is not None and (now - self._spike_low_ts) > 45.0:
            self._spike_low = None
            self._spike_low_ts = 0.0

        # [2] Recovery off spike low
        recovery = bid - self._spike_low if self._spike_low is not None else 0.0
        recovery_ok = recovery >= recovery_threshold

        # [3] Broad trend: bid > price 30s ago
        cutoff_30s = now - TREND_WINDOW_S
        old_prices = [p for t_, p in hist if t_ <= cutoff_30s]
        trend_ok = len(old_prices) > 0 and bid > old_prices[-1]
        trend_ref = old_prices[-1] if old_prices else bid

        # [4] Micro momentum
        micro_delta = 0.0
        micro_ok = False
        if len(prices) >= MICRO_TICKS + 1:
            micro_delta = bid - prices[-(MICRO_TICKS + 1)]
            micro_ok = micro_delta > 0

        score = sum([spike_ok, recovery_ok, trend_ok, micro_ok])
        passed = spike_ok and recovery_ok and trend_ok and micro_ok

        reason = (
            f"spike={drop_spike:.1f}pts({'OK' if spike_ok else f'need>={spike_threshold}'}) | "
            f"recovery={recovery:.2f}pts({'OK' if recovery_ok else f'need>={recovery_threshold}'}) | "
            f"trend={'UP' if trend_ok else 'DOWN'}(ref={trend_ref:.3f}) | "
            f"micro={'UP' if micro_ok else 'DOWN'}({micro_delta:+.3f}) | "
            f"score={score}/4"
        )

        if not passed:
            return self._hold(reason, bid, score)

        # Position sizing
        lot = self._calc_lot(self.balance)

        return {
            "signal": "BUY",
            "symbol": self.SYMBOL,
            "confidence": round(score / 4.0, 2),
            "reason": reason,
            "amount": lot,
            "meta": {
                "bid": bid,
                "score": score,
                "drop_spike": round(drop_spike, 2),
                "recovery": round(recovery, 2),
                "sl": round(bid - SL_POINTS, 3),
                "tp": round(bid + TP_POINTS, 3),
                "atr_zone": "elevated" if score >= 3 else "normal",

                # 🔥 calibration snapshot (important for ML/debugging)
                "thresholds": {
                    "spike_min": spike_threshold,
                    "recovery_min": recovery_threshold,
                }
            }
        }

    # ── Execution gate ────────────────────────────────────────
    async def should_execute(self, signal: dict) -> bool:
        # Cooldown check
        elapsed = time.time() - self._last_trade_ts
        if elapsed < COOLDOWN_SEC:
            remaining = int(COOLDOWN_SEC - elapsed)
            self.logger.info(f"[{self.NAME}] cooldown {remaining}s remaining")
            return False

        # Session cap
        if self._trade_count >= MAX_TRADES:
            self.logger.info(f"[{self.NAME}] session cap {MAX_TRADES} reached")
            return False

        # Bot running check
        try:
            async with httpx.AsyncClient() as client:
                status = await client.get(f"{BACKEND_URL}/api/bot/status", timeout=5)
                if not status.json().get("running", False):
                    self.logger.info(f"[{self.NAME}] bot not running")
                    return False
        except Exception as e:
            self.logger.error(f"[{self.NAME}] bot status check failed: {e}")
            return False

        return True

    # ── Override run() to update trade tracking after execution ──
    async def run(self):
        """Extends base run() to track trade count and cooldown."""
        # Call parent pipeline (fetch → signal → broadcast → gate → execute)
        signal = None
        try:
            market_data = await self.fetch_market_data()
            signal      = await self.compute_signal(market_data)

            log_id = await log_signal(signal, strategy_name=self.NAME)

            self.logger.info(
                f"[{self.NAME}] signal={signal['signal']} "
                f"confidence={signal.get('confidence', 0):.0%} "
                f"reason={signal.get('reason', '')}"
            )

            # Always broadcast
            await self.broadcast_fn({
                "symbol": self.SYMBOL,
                "action": signal["signal"],
                "signal": {
                    "direction": 1 if signal["signal"] == "BUY" else 0,
                    "rsi": 0,  # not computed — spike strategy doesn't use RSI
                    "adx": 0,  # not computed — spike strategy doesn't use ADX
                    "atr": round(signal["meta"].get("drop_spike", 0), 5),
                    "ema50": 0,  # not computed
                    "ema200": 0,  # not computed
                    "macd_hist": round(signal["meta"].get("recovery", 0), 5),
                    "tss_score": signal["meta"].get("score", 0),
                    "atr_zone": signal["meta"].get("atr_zone", "normal"),
                    "confidence": signal.get("confidence", 0),
                    "reason": signal.get("reason", ""),
                }
            })

            if signal["signal"] == "HOLD":
                return

            if not await self.should_execute(signal):
                return

            # Fire trade
            self.logger.info(
                f"[{self.NAME}] EXECUTING BUY {self.SYMBOL} lot={signal['amount']}"
            )
            result = await self.execute_trade_fn(
                symbol=self.SYMBOL,
                action="rise",
                amount=signal["amount"],
            )
            await mark_executed(log_id)
            self.logger.info(f"[{self.NAME}] trade result: {result}")

            # Update tracking
            self._trade_count   += 1
            self._last_trade_ts  = time.time()
            self._spike_low      = None   # reset after trade fires

        except Exception as e:
            self.logger.error(f"[{self.NAME}] pipeline error: {e}")

    # ── Helpers ───────────────────────────────────────────────
    def _hold(self, reason: str, bid: float, score: int = 0) -> dict:
        return {
            "signal": "HOLD",
            "symbol": self.SYMBOL,
            "confidence": round(score / 4.0, 2),
            "reason": reason,
            "amount": 0.0,
            "meta": {
                "bid": bid,
                "score": score,
                "drop_spike": 0.0,
                "recovery": 0.0,
                "atr_zone": "low",  # HOLD = no spike = low volatility
            }
        }

    def _calc_lot(self, balance: float) -> float:
        raw     = (balance * RISK_PCT) / SL_POINTS
        snapped = round(raw / 0.01) * 0.01
        return round(max(0.2, min(snapped, 290.0)), 2)

    def reset_session(self):
        self._trade_count  = 0
        self._spike_low    = None
        self._spike_low_ts = 0.0
        self.logger.info(f"[{self.NAME}] session reset")