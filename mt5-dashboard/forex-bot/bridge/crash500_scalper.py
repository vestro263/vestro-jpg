"""
crash500_scalper.py -- QUALITY ENTRY MODE (Deriv Crash 500 Index)

Thresholds tuned from live data observed 2026-03-23:
  - Typical drift:  0.01-0.05 pts per tick
  - Real spikes:    1.5 - 7.0 pts (seen 6.5pts in live session)
  - Normal range:   price moves ~0.1-0.3pts per 2s window

Entry checklist (ALL must pass):
  [1] SPIKE DEPTH     >= 3.0pts drop within last 8s (realistic for C500)
  [2] SPIKE RECOVERY  price bounced >= 0.5pts off spike low
  [3] TREND ALIGNED   price now > price 30s ago (broad uptrend intact)
  [4] MOMENTUM OK     last 5 ticks net positive (micro bounce confirmed)
  [5] NO OPEN TRADE   zero positions open on this symbol
  [6] COOLDOWN CLEAR  >= 300s (5 min) since last fill
  [7] SESSION CAP     < MAX_TRADES this session
"""

import logging
import time
import threading
from collections import deque

logger = logging.getLogger(__name__)

SYMBOL           = "Crash 500 Index"
POINT            = 1.0

# ── Risk
SL_POINTS        = 60.0
TP_POINTS        = 90.0
RISK_PCT         = 0.01

# ── Session discipline
MAX_OPEN         = 1       # 1 trade at a time, no stacking
MAX_TRADES       = 3       # 3 trades max per session
COOLDOWN_SEC     = 300     # 5 min between trades

# ── Entry quality filters — calibrated from live C500 data
MIN_SPIKE_PTS    = 3.0     # 3pt drop = real spike (not noise). Live data shows 1.7-6.5pt spikes
RECOVERY_PTS     = 0.5     # price must bounce 0.5pts off low before entry
SPIKE_WINDOW_S   = 8.0     # look back 8s for spike (spikes complete fast)
TREND_WINDOW_S   = 30.0    # 30s trend structure check
MICRO_TICKS      = 5       # last 5 ticks must be net positive

# ── Broker (verified 2026-03-23)
_VOL_MIN         = 0.2
_VOL_MAX         = 290.0
_VOL_STEP        = 0.01

POLL_MS          = 200     # 5 Hz — fast enough for C500 spikes, not wasteful


def _safe(text: str) -> str:
    return text.encode("ascii", errors="replace").decode("ascii")


class Crash500Scalper:

    def __init__(self, config, broadcast_fn, get_account_fn,
                 get_ohlcv_fn, send_order_fn, log_trade_fn, get_tick_fn,
                 get_symbol_info_fn=None, get_positions_fn=None):
        self.broadcast       = broadcast_fn
        self.get_account     = get_account_fn
        self.get_ohlcv       = get_ohlcv_fn
        self.send_order      = send_order_fn
        self.log_trade       = log_trade_fn
        self.get_tick        = get_tick_fn
        self.get_symbol_info = get_symbol_info_fn
        self.get_positions   = get_positions_fn

        self._running       = False
        self._thread        = None
        self._trade_count   = 0
        self._last_trade_ts = 0.0
        self._fire_lock     = threading.Event()
        self._price_history = deque(maxlen=3000)   # ~10 min at 5Hz
        self._tick_count    = 0

        self._vol_min  = _VOL_MIN
        self._vol_max  = _VOL_MAX
        self._vol_step = _VOL_STEP

        self._spike_low    = None
        self._spike_low_ts = 0.0

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def start(self):
        self._init_symbol()
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="Crash500Scalper"
        )
        self._thread.start()
        logger.info(_safe(
            f"Crash500Scalper QUALITY MODE | "
            f"MIN_SPIKE={MIN_SPIKE_PTS}pts | RECOVERY={RECOVERY_PTS}pts | "
            f"COOLDOWN={COOLDOWN_SEC}s | MAX_OPEN={MAX_OPEN} | MAX_TRADES={MAX_TRADES}"
        ))

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def reset_session(self):
        self._trade_count  = 0
        self._spike_low    = None
        self._spike_low_ts = 0.0
        logger.info(_safe("Session reset"))

    # ------------------------------------------------------------------
    # Symbol info
    # ------------------------------------------------------------------
    def _init_symbol(self):
        if not self.get_symbol_info:
            return
        try:
            info = self.get_symbol_info(SYMBOL)
            if info:
                self._vol_min  = float(info.get("volume_min",  _VOL_MIN))
                self._vol_max  = float(info.get("volume_max",  _VOL_MAX))
                self._vol_step = float(info.get("volume_step", _VOL_STEP))
                logger.info(_safe(
                    f"Symbol info: min={self._vol_min} "
                    f"step={self._vol_step} max={self._vol_max}"
                ))
        except Exception as e:
            logger.warning(_safe(f"Could not load symbol info: {e} -- using defaults"))

    # ------------------------------------------------------------------
    # Lot calc
    # ------------------------------------------------------------------
    def _calc_lot(self, balance: float) -> float:
        raw      = (balance * RISK_PCT) / SL_POINTS
        steps    = round(raw / self._vol_step)
        snapped  = round(steps * self._vol_step, 10)
        snapped  = max(self._vol_min, min(snapped, self._vol_max))
        decimals = (
            len(str(self._vol_step).rstrip("0").split(".")[-1])
            if "." in str(self._vol_step) else 0
        )
        return round(snapped, decimals)

    # ------------------------------------------------------------------
    # Open positions
    # ------------------------------------------------------------------
    def _open_count(self) -> int:
        if not self.get_positions:
            return 0
        try:
            positions = self.get_positions(SYMBOL)
            return len(positions) if positions else 0
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Market evaluation
    # ------------------------------------------------------------------
    def _evaluate(self, now: float, bid: float) -> tuple:
        hist = list(self._price_history)
        if len(hist) < 30:
            return False, "warming up", 0

        prices = [p for _, p in hist]

        # [1] Spike depth — look back SPIKE_WINDOW_S seconds
        cutoff_spike = now - SPIKE_WINDOW_S
        recent_spike = [p for t, p in hist if t >= cutoff_spike]
        high_spike   = max(recent_spike) if recent_spike else bid
        drop_spike   = high_spike - bid
        spike_ok     = drop_spike >= MIN_SPIKE_PTS

        # Track and update spike low
        if spike_ok:
            if self._spike_low is None or bid < self._spike_low:
                self._spike_low    = bid
                self._spike_low_ts = now
        # Expire spike low if stale (no spike for 45s)
        if self._spike_low is not None and (now - self._spike_low_ts) > 45.0:
            self._spike_low    = None
            self._spike_low_ts = 0.0

        # [2] Recovery off spike low
        recovery    = bid - self._spike_low if self._spike_low is not None else 0.0
        recovery_ok = recovery >= RECOVERY_PTS

        # [3] Trend: price now > price 30s ago
        cutoff_30s  = now - TREND_WINDOW_S
        old_prices  = [p for t, p in hist if t <= cutoff_30s]
        trend_ok    = len(old_prices) > 0 and bid > old_prices[-1]
        trend_ref   = old_prices[-1] if old_prices else bid

        # [4] Micro momentum: last MICRO_TICKS net positive
        micro_delta = 0.0
        micro_ok    = False
        if len(prices) >= MICRO_TICKS + 1:
            micro_delta = bid - prices[-(MICRO_TICKS + 1)]
            micro_ok    = micro_delta > 0

        score  = sum([spike_ok, recovery_ok, trend_ok, micro_ok])
        passed = spike_ok and recovery_ok and trend_ok and micro_ok

        reason = (
            f"spike={drop_spike:.1f}pts({'OK' if spike_ok else f'need>={MIN_SPIKE_PTS}'}) | "
            f"recovery={recovery:.2f}pts({'OK' if recovery_ok else f'need>={RECOVERY_PTS}'}) | "
            f"trend={'UP' if trend_ok else 'DOWN'}(ref={trend_ref:.3f}) | "
            f"micro={'UP' if micro_ok else 'DOWN'}({micro_delta:+.3f}) | "
            f"score={score}/4"
        )

        return passed, reason, score

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def _loop(self):
        logger.info(_safe("Crash500Scalper: evaluation loop active"))
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.error(_safe(f"Scalper error: {e}"))
            time.sleep(POLL_MS / 1000.0)

    def _tick(self):
        now = time.time()
        self._tick_count += 1

        try:
            tick = self.get_tick(SYMBOL)
            bid  = float(tick["bid"])
        except Exception as e:
            logger.error(_safe(f"Tick error: {e}"))
            return

        self._price_history.append((now, bid))

        should_enter, reason, score = self._evaluate(now, bid)
        cooldown = max(0, int(COOLDOWN_SEC - (now - self._last_trade_ts)))
        open_now = self._open_count()

        # Heartbeat every 10 ticks (~2s at 5Hz)
        if self._tick_count % 10 == 0:
            logger.info(_safe(
                f"C500 | {bid:.3f} | {reason} | "
                f"CD={cooldown}s | open={open_now} | trades={self._trade_count}/{MAX_TRADES}"
            ))

        self.broadcast({
            "type": "signal",
            "source": "crash500",  # ← add this
            "symbol": SYMBOL,
            "signal": {
                "signal": "BUY" if should_enter else "HOLD",  # ← add this
                "direction": 1 if should_enter else 0,
                "tss_score": score,
                "checklist_score": score,
                "reason": reason,
                "atr_zone": "elevated" if score >= 3 else "normal",
                "atr": 1.0,
                "confidence": round(score / 4.0, 2),
                "price": bid,
                "sl_distance": SL_POINTS,
                "tp1_distance": TP_POINTS,
            },
            "approved": (
                    should_enter
                    and not self._fire_lock.is_set()
                    and cooldown == 0
                    and open_now < MAX_OPEN
            ),
        })

        # Gates
        if not should_enter:
            return
        if self._fire_lock.is_set():
            return
        if cooldown > 0:
            logger.info(_safe(f"C500: READY but cooldown {cooldown}s remaining"))
            return
        if open_now >= MAX_OPEN:
            logger.info(_safe(f"C500: READY but {open_now} position still open -- waiting"))
            return
        if self._trade_count >= MAX_TRADES:
            logger.info(_safe(f"C500: session cap {MAX_TRADES} reached -- call reset_session()"))
            return

        # FIRE
        logger.info(_safe(f"C500: ALL CLEAR | {reason} | firing..."))
        self._fire_lock.set()
        threading.Thread(
            target=self._fire, args=(bid, reason, now),
            daemon=True, name="C500-Fire"
        ).start()

    # ------------------------------------------------------------------
    # Fire
    # ------------------------------------------------------------------
    def _fire(self, bid: float, reason: str, fired_at: float):
        try:
            t2    = self.get_tick(SYMBOL)
            entry = float(t2["ask"])
        except Exception:
            entry = bid

        try:
            account = self.get_account()
            balance = account.get("balance", 100.0)
            lot     = self._calc_lot(balance)
            sl      = round(entry - SL_POINTS * POINT, 3)
            tp      = round(entry + TP_POINTS * POINT, 3)

            logger.info(_safe(
                f"*** FIRE | Entry={entry} SL={sl} TP={tp} "
                f"Lot={lot} Bal=${balance:.2f} | {reason}"
            ))

            result = self.send_order(
                SYMBOL, "buy", lot,
                sl_price=sl, tp_price=tp,
                comment="C500 spike rebound",
            )

            self._trade_count  += 1
            self._last_trade_ts = fired_at
            self._spike_low     = None

            logger.info(_safe(
                f"FILLED | Ticket={result['ticket']} | "
                f"Lot={lot} | Entry={entry} | TP={tp} | SL={sl}"
            ))

            self.log_trade(
                ticket=result["ticket"], symbol=SYMBOL,
                direction="buy", lot_size=lot,
                entry=entry, sl=sl, tp1=tp, tp2=tp,
                tss_score=score if 'score' in dir() else 4,
                checklist=4,
                reason=reason,
                atr_zone="elevated",
            )

            self.broadcast({
                "type": "signal", "symbol": SYMBOL,
                "signal": {
                    "direction": 1, "tss_score": 4, "rsi": 50.0,
                    "atr": 1.0, "atr_zone": "elevated",
                    "atr_ratio": 1.0, "spike_ratio": 1.0,
                    "ema21": entry, "ema50": bid, "ema200": 0.0,
                    "adx": 40.0,
                    "di_plus": 0.0, "di_minus": 0.0, "macd_hist": 0.0,
                    "atr_avg": 1.0,
                    "sl_distance": SL_POINTS,
                    "tp1_distance": TP_POINTS,
                    "tp2_distance": TP_POINTS,
                    "checklist_score": 4,
                    "reason": f"SPIKE REBOUND {lot}lot | TP={tp} | {reason}",
                },
                "approved": True,
                "trade": {
                    "ticket": result["ticket"], "direction": "buy",
                    "lot_size": lot, "entry": entry, "sl": sl, "tp": tp,
                },
                "account": account,
            })

        except Exception as e:
            logger.error(_safe(f"ORDER FAILED: {e}"))
            self._last_trade_ts = fired_at

        finally:
            self._fire_lock.clear()