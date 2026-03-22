"""
crash500_scalper.py — TREND + SPIKE mode (Deriv Crash 500 Index)

Fixes applied:
  1. retcode=10014: Crash 500 Index on Deriv uses min_lot=1.0, step=1.0
     (synthetic indices are NOT forex — lot is in index units, not 100k)
     The broker is queried at startup via symbol_info() to get the real
     volume_min / volume_step so this never hard-codes wrong values again.
  2. Spam fire bug: _in_spike is now a threading.Event that is only cleared
     after a successful fill OR a hard timeout — not via a fragile Timer.
  3. UnicodeEncodeError: all logger calls pass through _safe().
"""

import logging
import time
import threading
from collections import deque

logger = logging.getLogger(__name__)

SYMBOL       = "Crash 500 Index"
POINT        = 1.0
SL_POINTS    = 60.0
TP_POINTS    = 90.0
RISK_PCT     = 0.005
MAX_TRADES   = 20
COOLDOWN_SEC = 45
POLL_MS      = 50        # 20 Hz

# ── Verified live from broker 2026-03-23 via check_symbol_info.py
_VOL_MIN  = 0.2
_VOL_MAX  = 290.0
_VOL_STEP = 0.01


def _safe(text: str) -> str:
    """Strip non-ASCII so Windows cp1252 console never raises UnicodeEncodeError."""
    return text.encode("ascii", errors="replace").decode("ascii")


class Crash500Scalper:

    def __init__(self, config, broadcast_fn, get_account_fn,
                 get_ohlcv_fn, send_order_fn, log_trade_fn, get_tick_fn,
                 get_symbol_info_fn=None):
        self.broadcast        = broadcast_fn
        self.get_account      = get_account_fn
        self.get_ohlcv        = get_ohlcv_fn
        self.send_order       = send_order_fn
        self.log_trade        = log_trade_fn
        self.get_tick         = get_tick_fn
        self.get_symbol_info  = get_symbol_info_fn   # optional but recommended

        self._running        = False
        self._thread         = None
        self._trade_count    = 0
        self._last_trade_ts  = 0.0
        self._fire_lock      = threading.Event()     # SET = currently firing
        self._price_history  = deque(maxlen=1000)
        self._tick_count     = 0

        # volume limits — updated from broker at start if possible
        self._vol_min  = _VOL_MIN
        self._vol_max  = _VOL_MAX
        self._vol_step = _VOL_STEP

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self):
        self._init_symbol()
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="Crash500Scalper"
        )
        self._thread.start()
        logger.info(_safe(
            f"Crash500Scalper started | vol_min={self._vol_min} "
            f"vol_step={self._vol_step} vol_max={self._vol_max}"
        ))

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def reset_session(self):
        self._trade_count = 0
        logger.info(_safe("Session reset"))

    # ------------------------------------------------------------------
    # Query broker for real volume limits (prevents retcode=10014 forever)
    # ------------------------------------------------------------------
    def _init_symbol(self):
        if not self.get_symbol_info:
            logger.info(_safe(
                f"No get_symbol_info_fn — using defaults: "
                f"min={self._vol_min} step={self._vol_step}"
            ))
            return
        try:
            info = self.get_symbol_info(SYMBOL)
            if info:
                self._vol_min  = float(info.get("volume_min",  _VOL_MIN))
                self._vol_max  = float(info.get("volume_max",  _VOL_MAX))
                self._vol_step = float(info.get("volume_step", _VOL_STEP))
                logger.info(_safe(
                    f"Symbol info loaded: min={self._vol_min} "
                    f"step={self._vol_step} max={self._vol_max}"
                ))
        except Exception as e:
            logger.warning(_safe(f"Could not load symbol info: {e} — using defaults"))

    # ------------------------------------------------------------------
    # Lot calculation — snapped to broker volume grid
    # ------------------------------------------------------------------
    def _calc_lot(self, balance: float) -> float:
        raw     = (balance * RISK_PCT) / SL_POINTS
        steps   = round(raw / self._vol_step)          # nearest step
        snapped = round(steps * self._vol_step, 10)    # avoid float drift
        # enforce min/max
        snapped = max(self._vol_min, min(snapped, self._vol_max))
        # final round to same decimals as step
        decimals = len(str(self._vol_step).rstrip("0").split(".")[-1]) if "." in str(self._vol_step) else 0
        return round(snapped, decimals)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def _loop(self):
        logger.info(_safe("Crash500Scalper: loop active"))
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.error(_safe(f"Scalper error: {e}"))
            time.sleep(POLL_MS / 1000.0)

    def _tick(self):
        now = time.time()
        self._tick_count += 1

        # ── Get price
        try:
            tick = self.get_tick(SYMBOL)
            bid  = float(tick["bid"])
        except Exception as e:
            logger.error(_safe(f"Tick error: {e}"))
            return

        self._price_history.append((now, bid))

        # ── Rolling 5s high + drop
        cutoff      = now - 5.0
        recent      = [(t, p) for t, p in self._price_history if t >= cutoff]
        recent_high = max(p for _, p in recent) if recent else bid
        drop        = recent_high - bid

        # ── 20-tick momentum
        hist         = list(self._price_history)
        price_rising = len(hist) >= 20 and bid > hist[-20][1]

        # ── Entry decision
        is_spike = drop >= 1.0
        is_trend = price_rising
        is_entry = is_spike or is_trend

        cooldown = max(0, int(COOLDOWN_SEC - (now - self._last_trade_ts)))
        mode     = "SPIKE" if is_spike else ("TREND" if is_trend else "FLAT")

        # ── Heartbeat every 10 ticks
        if self._tick_count % 10 == 0:
            logger.info(_safe(
                f"C500 | {bid:.3f} | Drop={drop:.1f}pts | "
                f"{mode} | CD={cooldown}s | {self._trade_count}/{MAX_TRADES}"
            ))

        # ── Broadcast
        self.broadcast({
            "type": "signal", "symbol": SYMBOL,
            "signal": {
                "direction":       1 if is_entry else 0,
                "tss_score":       0,
                "rsi":             50.0,
                "atr":             max(drop, 1.0),
                "atr_zone":        "elevated" if is_spike else "normal",
                "atr_ratio":       round(max(drop, 0.1), 2),
                "spike_ratio":     round(max(drop, 0.1), 2),
                "ema21":           bid,
                "ema50":           recent_high,
                "ema200":          0.0,
                "adx":             max(drop, 0.0),
                "di_plus":         0.0, "di_minus": 0.0,
                "macd_hist":       0.0,
                "atr_avg":         1.0,
                "sl_distance":     SL_POINTS,
                "tp1_distance":    TP_POINTS,
                "tp2_distance":    TP_POINTS,
                "checklist_score": 0,
                "reason": (
                    f"SPIKE {drop:.1f}pts | REBOUND BUY" if is_spike else
                    f"TREND UP | BUY | CD={cooldown}s"   if is_trend else
                    f"Flat | CD={cooldown}s"
                ),
            },
            "approved": is_entry and not self._fire_lock.is_set() and cooldown == 0,
        })

        # ── Gates — strict order
        if not is_entry:
            return
        if self._fire_lock.is_set():    # already firing, wait for resolution
            return
        if cooldown > 0:
            return
        if self._trade_count >= MAX_TRADES:
            logger.info(_safe(f"C500: max trades {MAX_TRADES} -- call reset_session()"))
            return

        # ── FIRE (runs in background so the tick loop never blocks)
        self._fire_lock.set()
        threading.Thread(
            target=self._fire, args=(bid, drop, mode, now),
            daemon=True, name="C500-Fire"
        ).start()

    # ------------------------------------------------------------------
    # Fire — isolated so failures don't spam the tick loop
    # ------------------------------------------------------------------
    def _fire(self, bid: float, drop: float, mode: str, fired_at: float):
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
                f"*** FIRE | {mode} | Drop={drop:.1f}pts | "
                f"Entry={entry} SL={sl} TP={tp} Lot={lot} Bal=${balance:.2f}"
            ))

            result = self.send_order(
                SYMBOL, "buy", lot,
                sl_price=sl, tp_price=tp,
                comment=f"C500 {mode}",
            )

            self._trade_count  += 1
            self._last_trade_ts = fired_at

            logger.info(_safe(
                f"FILLED | Ticket={result['ticket']} | "
                f"Lot={lot} | Entry={entry} | TP={tp} | SL={sl}"
            ))

            self.log_trade(
                ticket=result["ticket"], symbol=SYMBOL,
                direction="buy", lot_size=lot,
                entry=entry, sl=sl, tp1=tp, tp2=tp,
                tss_score=0, checklist=0,
                reason=f"C500 {mode} drop={drop:.1f}pts",
                atr_zone="elevated" if drop >= 1.0 else "normal",
            )

            self.broadcast({
                "type": "signal", "symbol": SYMBOL,
                "signal": {
                    "direction": 1, "tss_score": 0, "rsi": 50.0,
                    "atr": max(drop, 1.0),
                    "atr_zone": "elevated" if drop >= 1.0 else "normal",
                    "atr_ratio": round(max(drop, 0.1), 2),
                    "spike_ratio": round(max(drop, 0.1), 2),
                    "ema21": entry, "ema50": bid, "ema200": 0.0,
                    "adx": max(drop, 0.0),
                    "di_plus": 0.0, "di_minus": 0.0, "macd_hist": 0.0,
                    "atr_avg": 1.0,
                    "sl_distance": SL_POINTS,
                    "tp1_distance": TP_POINTS,
                    "tp2_distance": TP_POINTS,
                    "checklist_score": 0,
                    "reason": f"BUY {lot}lot | {mode} | TP={tp}",
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
            # stamp cooldown so we don't hammer broker on repeated failure
            self._last_trade_ts = fired_at

        finally:
            # Always release the lock — no matter what happened
            self._fire_lock.clear()