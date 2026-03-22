"""
signal_bridge_py.py — Pure Python/NumPy signal engine.
Drop-in replacement for the C++ signal_bridge.py.
Produces identical output fields to CSignal struct.
No build step required.
"""

import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)


# ── Indicators ─────────────────────────────────────────────────────────────

def calc_ema(close: np.ndarray, period: int) -> np.ndarray:
    out = np.zeros(len(close))
    k   = 2.0 / (period + 1.0)
    out[0] = close[0]
    for i in range(1, len(close)):
        out[i] = close[i] * k + out[i - 1] * (1.0 - k)
    return out


def calc_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    n   = len(close)
    out = np.zeros(n)
    if n < period + 1:
        return out
    deltas = np.diff(close)
    gain   = np.where(deltas > 0, deltas, 0.0)
    loss   = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = np.mean(gain[:period])
    avg_l  = np.mean(loss[:period])
    out[period] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    for i in range(period + 1, n):
        avg_g = (avg_g * (period - 1) + gain[i - 1]) / period
        avg_l = (avg_l * (period - 1) + loss[i - 1]) / period
        out[i] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return out


def calc_atr(high: np.ndarray, low: np.ndarray,
             close: np.ndarray, period: int = 14) -> np.ndarray:
    n  = len(close)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i]  - close[i - 1]),
            abs(low[i]   - close[i - 1]),
        )
    out    = np.zeros(n)
    out[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def calc_macd(close: np.ndarray,
              fast: int = 12, slow: int = 26,
              signal: int = 9):
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    sig_line  = calc_ema(macd_line, signal)
    histogram = macd_line - sig_line
    return macd_line, sig_line, histogram


def calc_adx(high: np.ndarray, low: np.ndarray,
             close: np.ndarray, period: int = 14):
    n      = len(close)
    tr     = np.zeros(n)
    pdm    = np.zeros(n)
    ndm    = np.zeros(n)

    for i in range(1, n):
        tr[i]  = max(high[i] - low[i],
                     abs(high[i] - close[i - 1]),
                     abs(low[i]  - close[i - 1]))
        up   = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        pdm[i] = up   if (up > down and up > 0)   else 0.0
        ndm[i] = down if (down > up and down > 0) else 0.0

    def wilder(src):
        out = np.zeros(n)
        out[period] = np.sum(src[1:period + 1])
        for i in range(period + 1, n):
            out[i] = out[i - 1] - out[i - 1] / period + src[i]
        return out

    str_  = wilder(tr)
    spdm  = wilder(pdm)
    sndm  = wilder(ndm)

    dip = np.zeros(n)
    dim = np.zeros(n)
    dx  = np.zeros(n)

    for i in range(period, n):
        dip[i] = 100.0 * spdm[i] / str_[i] if str_[i] > 0 else 0.0
        dim[i] = 100.0 * sndm[i] / str_[i] if str_[i] > 0 else 0.0
        dsum   = dip[i] + dim[i]
        dx[i]  = 100.0 * abs(dip[i] - dim[i]) / dsum if dsum > 0 else 0.0

    adx = np.zeros(n)
    start = period * 2
    if start < n:
        adx[start - 1] = np.mean(dx[period:start])
        for i in range(start, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx, dip, dim


# ── ATR zone ───────────────────────────────────────────────────────────────

def get_atr_zone(ratio: float) -> str:
    if ratio > 2.5: return "extreme"
    if ratio > 1.5: return "elevated"
    if ratio < 0.5: return "low"
    return "normal"


# ── Checklist ──────────────────────────────────────────────────────────────

def score_checklist(direction, ema50, ema200, rsi,
                    macd_hist, volume, vol_avg, atr_ratio) -> int:
    score = 0
    if direction == 1:
        if ema50 > ema200:                       score += 1
        if 30 <= rsi <= 45:                      score += 1
        if macd_hist > 0:                        score += 1
        if volume > vol_avg * 1.2:               score += 1
        if 0.5 < atr_ratio < 2.5:               score += 1
        score += 2  # zone + candle scored externally
    elif direction == -1:
        if ema50 < ema200:                       score += 1
        if 55 <= rsi <= 70:                      score += 1
        if macd_hist < 0:                        score += 1
        if volume > vol_avg * 1.2:               score += 1
        if 0.5 < atr_ratio < 2.5:               score += 1
        score += 2
    return min(score, 7)


# ── Main signal function ───────────────────────────────────────────────────

def get_signal(df: pd.DataFrame) -> dict:
    """
    Evaluate signal on OHLCV DataFrame.
    Returns dict matching C++ CSignal fields exactly.
    """
    n = len(df)
    if n < 210:
        return {
            "direction": 0, "tss_score": 0,
            "rsi": 0.0, "atr": 0.0, "atr_avg": 0.0, "atr_ratio": 1.0,
            "ema21": 0.0, "ema50": 0.0, "ema200": 0.0,
            "adx": 0.0, "di_plus": 0.0, "di_minus": 0.0,
            "macd_hist": 0.0,
            "sl_distance": 0.0, "tp1_distance": 0.0, "tp2_distance": 0.0,
            "atr_zone": "normal", "reason": "Insufficient bars (need 210+)",
            "checklist_score": 0,
        }

    close  = df["close"].values.astype(np.float64)
    high   = df["high"].values.astype(np.float64)
    low    = df["low"].values.astype(np.float64)
    volume = df["volume"].values.astype(np.float64)

    ema21  = calc_ema(close, 21)
    ema50  = calc_ema(close, 50)
    ema200 = calc_ema(close, 200)
    rsi    = calc_rsi(close, 14)
    atr    = calc_atr(high, low, close, 14)
    _, _, macd_hist = calc_macd(close, 12, 26, 9)
    adx, dip, dim  = calc_adx(high, low, close, 14)

    i = n - 1

    atr_avg   = float(np.mean(atr[max(0, i - 19): i + 1]))
    atr_ratio = atr[i] / atr_avg if atr_avg > 0 else 1.0
    atr_zone  = get_atr_zone(atr_ratio)

    # ── TSS scoring ────────────────────────────────────────────────────────
    bull_stack = ema21[i] > ema50[i] > ema200[i]
    bear_stack = ema21[i] < ema50[i] < ema200[i]

    tss = 0
    if bull_stack or bear_stack:                              tss += 1
    if adx[i] > 25.0:                                        tss += 1
    if close[i] > ema200[max(0, i - 50)]:                    tss += 1
    if (bull_stack and macd_hist[i] > 0) or \
       (bear_stack and macd_hist[i] < 0):                    tss += 1
    vol_avg = float(np.mean(volume[max(0, i - 19): i + 1]))
    if volume[i] > vol_avg:                                   tss += 1

    # ── Direction ──────────────────────────────────────────────────────────
    atr_ok = atr_zone != "extreme"

    if bull_stack and 30 <= rsi[i] <= 45 and tss >= 3 and atr_ok:
        direction = 1
    elif bear_stack and 55 <= rsi[i] <= 70 and tss >= 3 and atr_ok:
        direction = -1
    else:
        direction = 0

    # ── SL / TP ────────────────────────────────────────────────────────────
    sl_dist  = float(atr[i]) * 1.5
    tp1_dist = sl_dist * 1.5
    tp2_dist = sl_dist * 3.0

    checklist = score_checklist(
        direction, float(ema50[i]), float(ema200[i]),
        float(rsi[i]), float(macd_hist[i]),
        float(volume[i]), vol_avg, atr_ratio,
    )

    # ── Reason ────────────────────────────────────────────────────────────
    if direction == 1:
        reason = (f"BUY | TSS={tss} | RSI={rsi[i]:.1f} | "
                  f"ADX={adx[i]:.1f} | ATR_zone={atr_zone} | "
                  f"MACD={macd_hist[i]:.5f}")
    elif direction == -1:
        reason = (f"SELL | TSS={tss} | RSI={rsi[i]:.1f} | "
                  f"ADX={adx[i]:.1f} | ATR_zone={atr_zone} | "
                  f"MACD={macd_hist[i]:.5f}")
    else:
        stack = "bull" if bull_stack else ("bear" if bear_stack else "none")
        reason = (f"FLAT | TSS={tss} | RSI={rsi[i]:.1f} | "
                  f"ADX={adx[i]:.1f} | stack={stack} | ATR_zone={atr_zone}")

    return {
        "direction":       int(direction),
        "tss_score":       int(tss),
        "rsi":             round(float(rsi[i]),        2),
        "atr":             round(float(atr[i]),        6),
        "atr_avg":         round(float(atr_avg),       6),
        "atr_ratio":       round(float(atr_ratio),     3),
        "ema21":           round(float(ema21[i]),      6),
        "ema50":           round(float(ema50[i]),      6),
        "ema200":          round(float(ema200[i]),     6),
        "adx":             round(float(adx[i]),        2),
        "di_plus":         round(float(dip[i]),        2),
        "di_minus":        round(float(dim[i]),        2),
        "macd_hist":       round(float(macd_hist[i]),  8),
        "sl_distance":     round(sl_dist,              6),
        "tp1_distance":    round(tp1_dist,             6),
        "tp2_distance":    round(tp2_dist,             6),
        "atr_zone":        atr_zone,
        "reason":          reason,
        "checklist_score": int(checklist),
    }