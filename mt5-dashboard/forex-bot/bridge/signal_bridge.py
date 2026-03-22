"""
Signal Bridge — loads the compiled C++ forex_engine shared library
and exposes evaluate_signal() as a Python function.
"""

import ctypes
import logging
import os
import platform
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

# ── Locate the compiled library ────────────────────────────────────────────
def _find_library() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    system = platform.system()
    candidates = []

    if system == "Windows":
        candidates = [
            os.path.join(base, "engine", "build", "Release", "forex_engine.dll"),
            os.path.join(base, "engine", "build", "forex_engine.dll"),
            os.path.join(base, "forex_engine.dll"),
        ]
    else:
        candidates = [
            os.path.join(base, "engine", "build", "libforex_engine.so"),
            os.path.join(base, "engine", "build", "libforex_engine.dylib"),
            os.path.join(base, "libforex_engine.so"),
        ]

    for path in candidates:
        if os.path.exists(path):
            logger.info(f"Found C++ library: {path}")
            return path

    raise FileNotFoundError(
        "forex_engine library not found. "
        "Run: cd engine && mkdir build && cd build && "
        "cmake .. -DCMAKE_BUILD_TYPE=Release && cmake --build . --config Release"
    )


# ── ctypes Signal struct ───────────────────────────────────────────────────
class CSignal(ctypes.Structure):
    _fields_ = [
        ("direction",       ctypes.c_int),
        ("tss_score",       ctypes.c_int),
        ("rsi",             ctypes.c_double),
        ("atr",             ctypes.c_double),
        ("atr_avg",         ctypes.c_double),
        ("atr_ratio",       ctypes.c_double),
        ("ema21",           ctypes.c_double),
        ("ema50",           ctypes.c_double),
        ("ema200",          ctypes.c_double),
        ("adx",             ctypes.c_double),
        ("di_plus",         ctypes.c_double),
        ("di_minus",        ctypes.c_double),
        ("macd_hist",       ctypes.c_double),
        ("sl_distance",     ctypes.c_double),
        ("tp1_distance",    ctypes.c_double),
        ("tp2_distance",    ctypes.c_double),
        ("atr_zone",        ctypes.c_char * 16),
        ("reason",          ctypes.c_char * 256),
        ("checklist_score", ctypes.c_int),
    ]


# ── Library loader (singleton) ─────────────────────────────────────────────
_lib: Optional[ctypes.CDLL] = None

def _get_lib() -> ctypes.CDLL:
    global _lib
    if _lib is None:
        path = _find_library()
        _lib = ctypes.CDLL(path)

        _lib.evaluate_signal.restype  = CSignal
        _lib.evaluate_signal.argtypes = [
            ctypes.POINTER(ctypes.c_double),  # close
            ctypes.POINTER(ctypes.c_double),  # high
            ctypes.POINTER(ctypes.c_double),  # low
            ctypes.POINTER(ctypes.c_double),  # volume
            ctypes.c_int,                     # n
        ]

        # Also expose raw indicator functions for testing
        _lib.calc_ema.restype  = None
        _lib.calc_ema.argtypes = [
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_double),
        ]
        _lib.calc_rsi.restype  = None
        _lib.calc_rsi.argtypes = [
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_double),
        ]
        _lib.calc_atr.restype  = None
        _lib.calc_atr.argtypes = [
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_double),
        ]

    return _lib


def _to_ptr(arr: np.ndarray) -> ctypes.POINTER(ctypes.c_double):
    arr = arr.astype(np.float64, copy=False)
    return arr.ctypes.data_as(ctypes.POINTER(ctypes.c_double))


# ── Public API ─────────────────────────────────────────────────────────────
def get_signal(df) -> dict:
    """
    Run C++ signal engine on a DataFrame with OHLCV columns.
    Returns a plain dict with all indicator values and direction.
    """
    lib = _get_lib()
    n   = len(df)

    close  = df["close"].values.astype(np.float64)
    high   = df["high"].values.astype(np.float64)
    low    = df["low"].values.astype(np.float64)
    volume = df["volume"].values.astype(np.float64)

    sig = lib.evaluate_signal(
        _to_ptr(close), _to_ptr(high),
        _to_ptr(low),   _to_ptr(volume),
        ctypes.c_int(n)
    )

    return {
        "direction":       sig.direction,
        "tss_score":       sig.tss_score,
        "rsi":             round(sig.rsi, 2),
        "atr":             round(sig.atr, 6),
        "atr_avg":         round(sig.atr_avg, 6),
        "atr_ratio":       round(sig.atr_ratio, 3),
        "ema21":           round(sig.ema21, 6),
        "ema50":           round(sig.ema50, 6),
        "ema200":          round(sig.ema200, 6),
        "adx":             round(sig.adx, 2),
        "di_plus":         round(sig.di_plus, 2),
        "di_minus":        round(sig.di_minus, 2),
        "macd_hist":       round(sig.macd_hist, 8),
        "sl_distance":     round(sig.sl_distance, 6),
        "tp1_distance":    round(sig.tp1_distance, 6),
        "tp2_distance":    round(sig.tp2_distance, 6),
        "atr_zone":        sig.atr_zone.decode("utf-8").strip("\x00"),
        "reason":          sig.reason.decode("utf-8").strip("\x00"),
        "checklist_score": sig.checklist_score,
    }


def compute_ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Expose raw EMA computation for external use."""
    lib = _get_lib()
    out = np.zeros(len(prices), dtype=np.float64)
    lib.calc_ema(_to_ptr(prices), ctypes.c_int(len(prices)),
                 ctypes.c_int(period), _to_ptr(out))
    return out


def compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    lib = _get_lib()
    out = np.zeros(len(close), dtype=np.float64)
    lib.calc_rsi(_to_ptr(close), ctypes.c_int(len(close)),
                 ctypes.c_int(period), _to_ptr(out))
    return out


def compute_atr(high: np.ndarray, low: np.ndarray,
                close: np.ndarray, period: int = 14) -> np.ndarray:
    lib = _get_lib()
    out = np.zeros(len(close), dtype=np.float64)
    lib.calc_atr(_to_ptr(high), _to_ptr(low), _to_ptr(close),
                 ctypes.c_int(len(close)), ctypes.c_int(period), _to_ptr(out))
    return out