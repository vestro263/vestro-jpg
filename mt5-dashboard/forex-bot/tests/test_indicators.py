"""
Unit tests for the C++ indicator library.
Validates EMA, RSI, ATR outputs against known reference values
calculated in pure Python.
"""

import math
import sys
import os
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Reference implementations in Python ───────────────────────────────────
def py_ema(prices: np.ndarray, period: int) -> np.ndarray:
    k   = 2.0 / (period + 1.0)
    out = np.zeros(len(prices))
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i - 1] * (1.0 - k)
    return out


def py_rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.zeros(len(prices))
    if len(prices) < period + 1:
        return out
    gains = np.where(np.diff(prices) > 0, np.diff(prices), 0)
    losses = np.where(np.diff(prices) < 0, -np.diff(prices), 0)
    avg_g = np.mean(gains[:period])
    avg_l = np.mean(losses[:period])
    for i in range(period, len(prices)):
        g = max(prices[i] - prices[i - 1], 0)
        l = max(prices[i - 1] - prices[i], 0)
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
        out[i] = 100.0 if avg_l == 0 else 100.0 - (100.0 / (1.0 + avg_g / avg_l))
    return out


def py_atr(high, low, close, period=14):
    n   = len(close)
    out = np.zeros(n)
    for i in range(1, n):
        tr = max(high[i] - low[i],
                 abs(high[i] - close[i - 1]),
                 abs(low[i]  - close[i - 1]))
        out[i] = (out[i - 1] * (period - 1) + tr) / period if i >= period \
                 else tr
    return out


# ── Fixture: synthetic price data ─────────────────────────────────────────
@pytest.fixture
def price_series():
    np.random.seed(42)
    n      = 500
    close  = 1.1000 + np.cumsum(np.random.randn(n) * 0.0005)
    high   = close + np.abs(np.random.randn(n) * 0.0010)
    low    = close - np.abs(np.random.randn(n) * 0.0010)
    volume = np.random.randint(1000, 5000, n).astype(float)
    return close, high, low, volume


# ── Try to load C++ lib; skip all tests if not built ──────────────────────
try:
    from bridge.signal_bridge import compute_ema, compute_rsi, compute_atr, get_signal
    import pandas as pd
    CPP_AVAILABLE = True
except (FileNotFoundError, OSError):
    CPP_AVAILABLE = False

skip_if_no_cpp = pytest.mark.skipif(
    not CPP_AVAILABLE,
    reason="C++ library not built. Run cmake build first."
)


# ── EMA tests ──────────────────────────────────────────────────────────────
@skip_if_no_cpp
def test_ema_50_matches_python(price_series):
    close, *_ = price_series
    cpp_ema = compute_ema(close, 50)
    py_ema_ = py_ema(close, 50)
    # Last 100 values should match within floating point tolerance
    np.testing.assert_allclose(cpp_ema[-100:], py_ema_[-100:], rtol=1e-6)


@skip_if_no_cpp
def test_ema_200_matches_python(price_series):
    close, *_ = price_series
    cpp_ema  = compute_ema(close, 200)
    py_ema_  = py_ema(close, 200)
    np.testing.assert_allclose(cpp_ema[-100:], py_ema_[-100:], rtol=1e-6)


@skip_if_no_cpp
def test_ema_monotonically_converges(price_series):
    """EMA with larger period is smoother — std dev should be lower."""
    close, *_ = price_series
    ema_21  = compute_ema(close, 21)
    ema_200 = compute_ema(close, 200)
    assert np.std(ema_21[-200:]) > np.std(ema_200[-200:])


# ── RSI tests ──────────────────────────────────────────────────────────────
@skip_if_no_cpp
def test_rsi_bounded(price_series):
    close, *_ = price_series
    rsi = compute_rsi(close, 14)
    valid = rsi[15:]  # skip warmup
    assert np.all(valid >= 0)
    assert np.all(valid <= 100)


@skip_if_no_cpp
def test_rsi_matches_python(price_series):
    close, *_ = price_series
    cpp_rsi = compute_rsi(close, 14)
    py_rsi_ = py_rsi(close, 14)
    np.testing.assert_allclose(cpp_rsi[-100:], py_rsi_[-100:], rtol=1e-5)


@skip_if_no_cpp
def test_rsi_all_gains_is_100(price_series):
    """All-up price series → RSI should approach 100."""
    prices = np.linspace(1.0, 2.0, 50)  # purely rising
    rsi    = compute_rsi(prices, 14)
    assert rsi[-1] > 90.0


@skip_if_no_cpp
def test_rsi_all_losses_is_0(price_series):
    """All-down price series → RSI should approach 0."""
    prices = np.linspace(2.0, 1.0, 50)  # purely falling
    rsi    = compute_rsi(prices, 14)
    assert rsi[-1] < 10.0


# ── ATR tests ──────────────────────────────────────────────────────────────
@skip_if_no_cpp
def test_atr_positive(price_series):
    close, high, low, _ = price_series
    atr = compute_atr(high, low, close, 14)
    assert np.all(atr[15:] > 0)


@skip_if_no_cpp
def test_atr_matches_python(price_series):
    close, high, low, _ = price_series
    cpp_atr = compute_atr(high, low, close, 14)
    py_atr_ = py_atr(high, low, close, 14)
    np.testing.assert_allclose(cpp_atr[-50:], py_atr_[-50:], rtol=1e-5)


# ── Signal integration tests ───────────────────────────────────────────────
@skip_if_no_cpp
def test_signal_returns_all_fields(price_series):
    close, high, low, volume = price_series
    df = pd.DataFrame({
        "open": close, "high": high, "low": low,
        "close": close, "volume": volume,
        "time": pd.date_range("2024-01-01", periods=len(close), freq="h"),
    })
    sig = get_signal(df)
    required = ["direction", "tss_score", "rsi", "atr", "ema50", "ema200",
                "adx", "macd_hist", "sl_distance", "tp1_distance",
                "tp2_distance", "atr_zone", "reason", "checklist_score"]
    for field in required:
        assert field in sig, f"Missing field: {field}"


@skip_if_no_cpp
def test_signal_direction_valid(price_series):
    close, high, low, volume = price_series
    df = pd.DataFrame({
        "open": close, "high": high, "low": low,
        "close": close, "volume": volume,
        "time": pd.date_range("2024-01-01", periods=len(close), freq="h"),
    })
    sig = get_signal(df)
    assert sig["direction"] in (-1, 0, 1)


@skip_if_no_cpp
def test_signal_tss_bounded(price_series):
    close, high, low, volume = price_series
    df = pd.DataFrame({
        "open": close, "high": high, "low": low,
        "close": close, "volume": volume,
        "time": pd.date_range("2024-01-01", periods=len(close), freq="h"),
    })
    sig = get_signal(df)
    assert 0 <= sig["tss_score"] <= 5


@skip_if_no_cpp
def test_sl_tp_distances_positive(price_series):
    close, high, low, volume = price_series
    df = pd.DataFrame({
        "open": close, "high": high, "low": low,
        "close": close, "volume": volume,
        "time": pd.date_range("2024-01-01", periods=len(close), freq="h"),
    })
    sig = get_signal(df)
    assert sig["sl_distance"]  > 0
    assert sig["tp1_distance"] > sig["sl_distance"]
    assert sig["tp2_distance"] > sig["tp1_distance"]


@skip_if_no_cpp
def test_tp2_is_double_tp1(price_series):
    """TP2 distance should be exactly 2× TP1 distance (3R vs 1.5R)."""
    close, high, low, volume = price_series
    df = pd.DataFrame({
        "open": close, "high": high, "low": low,
        "close": close, "volume": volume,
        "time": pd.date_range("2024-01-01", periods=len(close), freq="h"),
    })
    sig = get_signal(df)
    ratio = sig["tp2_distance"] / sig["tp1_distance"]
    assert abs(ratio - 2.0) < 0.01, f"Expected TP2/TP1=2.0, got {ratio:.3f}"


# ── Risk manager tests (pure Python, no C++) ──────────────────────────────
def test_risk_manager_import():
    from bridge.risk_manager import RiskManager
    import yaml
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    rm = RiskManager(config)
    assert rm.risk_pct == 0.01
    assert rm.max_daily_loss_pct == 0.05


def test_lot_size_calculation():
    from bridge.risk_manager import RiskManager
    import yaml
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    rm       = RiskManager(config)
    balance  = 10_000.0
    sl_dist  = 0.0020  # 20 pips
    pip_val  = 1.0     # $1 per pip per mini lot
    lot      = rm.calc_lot_size(balance, sl_dist, pip_val)
    # Expected: (10000 * 0.01) / (20 * 1.0) = 5.0 lots
    assert abs(lot - 5.0) < 0.1


def test_daily_loss_limit():
    from bridge.risk_manager import RiskManager
    import yaml
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    rm = RiskManager(config)
    rm._daily_loss_usd = 600.0  # 6% of $10,000
    with pytest.raises(RuntimeError, match="Daily loss limit"):
        rm.check_daily_limit(10_000.0)


def test_atr_zone_detection():
    from bridge.risk_manager import RiskManager
    import yaml
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    rm = RiskManager(config)
    assert rm.get_atr_zone(0.001, 0.003) == "low"       # 0.33x
    assert rm.get_atr_zone(0.003, 0.003) == "normal"    # 1.0x
    assert rm.get_atr_zone(0.005, 0.003) == "elevated"  # 1.67x
    assert rm.get_atr_zone(0.009, 0.003) == "extreme"   # 3.0x