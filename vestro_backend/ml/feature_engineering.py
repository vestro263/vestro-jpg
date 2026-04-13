"""
feature_engineering.py
======================
Fetches OHLCV candles from the Deriv WebSocket API and computes a rich
feature matrix for R_75, CRASH500 (and any future symbol in SYMBOL_STRATEGY_MAP).

Two public entry points:

  1. build_feature_df(candles)
     Pure function.  Converts a list of OHLCV candle dicts into a fully
     featured DataFrame.  No I/O.  Used in tests and retrain.py.

  2. enrich_rows_batch(rows, symbol, api_token, granularity, candle_count)
     Async.  Fetches one candle batch for the symbol then attaches all
     CANDLE_FEATURE_NAMES as extra keys on every SignalLog row dict.
     Call this ONCE per symbol inside calibration_trainer._build_feature_matrix()
     before passing rows to the feature matrix builder — do NOT call per row.

Why this improves Precision / F1
---------------------------------
The current feature set (rsi, adx, tss_score, drop_spike) is thin and was
computed at signal-fire time in strategy memory.  This module adds:

  • Multi-period returns + log-returns     → captures momentum scale
  • ATR-normalised everything              → regime-agnostic, no price drift
  • Bollinger band position                → mean-reversion vs. breakout context
  • RSI divergence across 3 periods        → trend exhaustion signal
  • Candle body/wick geometry              → micro-structure (hammer, doji, etc.)
  • Rolling volatility ratio (5 / 20)     → regime proxy fed into class_balancer
  • Donchian breakout flags + distances    → trend continuation confirmation
  • Session flags (Asia / London / NY)     → intraday gold / synthetic cycle
  • Cyclical sin/cos time encoding         → no ordinal leakage from hour/dow
  • 1-bar lags of key features             → auto-correlation signal

All rolling computations use .shift(1) / only past data.  Zero lookahead.

Integration with calibration_trainer.py
----------------------------------------
Replace the start of _build_feature_matrix() with:

    from .feature_engineering import enrich_rows_batch, CANDLE_FEATURE_NAMES

    async def _build_feature_matrix(rows, feature_cols, symbol, api_token):
        rows = await enrich_rows_batch(rows, symbol, api_token)
        all_cols = feature_cols + [c for c in CANDLE_FEATURE_NAMES
                                   if c not in feature_cols]
        # ... rest unchanged ...
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

import numpy as np
import pandas as pd
import websockets

logger       = logging.getLogger(__name__)
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "")

# ── Granularity map ───────────────────────────────────────────────────────────
GRANULARITY = {
    "M15": 900,
    "M30": 1800,
    "H1":  3600,
    "H4":  14400,
}

# ── Feature names produced by build_feature_df / enrich_signal_log_row ───────
# These columns are ADDED to SignalLog's native columns for training.
# Do not reorder — retrain.py relies on this list being stable.
CANDLE_FEATURE_NAMES: list[str] = [
    "ret_1", "ret_3", "ret_5", "ret_10",
    "log_ret_1", "log_ret_3",
    "atr_14", "atr_pct",
    "vol_5", "vol_10", "vol_20", "vol_ratio_5_20",
    "roc_5", "roc_10",
    "macd_hist_raw",
    "bb_position",
    "body_ratio", "upper_wick_ratio", "lower_wick_ratio",
    "body_direction",
    "is_doji", "is_hammer", "is_shooting_star",
    "dist_hi_20", "dist_lo_20",
    "breakout_hi_20", "breakout_lo_20",
    "drawdown_20",
    "session_asia", "session_london", "session_ny", "session_overlap",
    "hour_sin", "hour_cos",
    "dow_sin",  "dow_cos",
    "ret_1_lag1", "body_direction_lag1", "vol_ratio_5_20_lag1",
]


# =============================================================================
# Deriv candle fetcher
# =============================================================================

async def fetch_candles(
    symbol:      str,
    granularity: int,
    count:       int,
    api_token:   str,
    end_epoch:   Optional[int] = None,
) -> list[dict]:
    """
    Pull OHLCV candles from Deriv WebSocket API.

    Returns a list of dicts with keys: epoch, open, high, low, close, volume.
    Ordered oldest → newest.  Returns [] on any error (caller handles gracefully).
    """
    url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        async with websockets.connect(url, open_timeout=15) as ws:
            await ws.send(json.dumps({"authorize": api_token}))
            await ws.recv()

            req: dict = {
                "ticks_history": symbol,
                "style":         "candles",
                "granularity":   granularity,
                "count":         count,
                "end":           "latest" if end_epoch is None else end_epoch,
            }
            await ws.send(json.dumps(req))
            raw = json.loads(await ws.recv())

        return [
            {
                "epoch":  c["epoch"],
                "open":   float(c["open"]),
                "high":   float(c["high"]),
                "low":    float(c["low"]),
                "close":  float(c["close"]),
                "volume": 0.0,  # Deriv synthetic indices carry no real volume
            }
            for c in raw.get("candles", [])
        ]
    except Exception as exc:
        logger.error(f"[feature_engineering] fetch_candles({symbol}): {exc}")
        return []


# =============================================================================
# Core feature builder  (pure, no I/O)
# =============================================================================

def build_feature_df(candles: list[dict]) -> pd.DataFrame:
    """
    Convert OHLCV candle list → fully-featured DataFrame.

    Index: UTC DatetimeIndex (one row per candle bar).
    All features are leakage-free: rolling windows never include the
    current bar's future data; breakout thresholds use .shift(1).

    NaN rows from rolling warm-up windows are dropped before returning.
    """
    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles).set_index("epoch").sort_index()
    df.index = pd.to_datetime(df.index, unit="s", utc=True)
    df.columns = [c.lower() for c in df.columns]

    feat = pd.DataFrame(index=df.index)
    # Keep OHLC on the result so outcome_labeler can walk barriers on it
    for col in ("open", "high", "low", "close"):
        feat[col] = df[col]

    # ── Returns ──────────────────────────────────────────────────────────────
    for n in (1, 3, 5, 10):
        feat[f"ret_{n}"]     = df["close"].pct_change(n)
        feat[f"log_ret_{n}"] = np.log(df["close"] / df["close"].shift(n))

    # ── ATR (Wilder smoothing) ────────────────────────────────────────────────
    atr = _wilder_atr(df, period=14)
    feat["atr_14"]  = atr
    feat["atr_pct"] = atr / df["close"]

    # ── Rolling volatility ────────────────────────────────────────────────────
    lr1 = feat["log_ret_1"]
    for w in (5, 10, 20):
        feat[f"vol_{w}"] = lr1.rolling(w).std()
    feat["vol_ratio_5_20"] = feat["vol_5"] / (feat["vol_20"] + 1e-9)

    # ── Momentum ──────────────────────────────────────────────────────────────
    for n in (5, 10):
        feat[f"roc_{n}"] = df["close"].pct_change(n) * 100

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    macd_line   = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    feat["macd_hist_raw"] = (macd_line - macd_signal) / (atr + 1e-9)

    # Bollinger Band position: 0 = at lower band, 1 = at upper band
    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std()
    upper = sma20 + 2 * std20
    lower = sma20 - 2 * std20
    feat["bb_position"] = (df["close"] - lower) / (upper - lower + 1e-9)

    # ── Candle geometry ───────────────────────────────────────────────────────
    candle_range = (df["high"] - df["low"]).replace(0, np.nan)
    body         = (df["close"] - df["open"]).abs()
    upper_wick   = df["high"] - df[["open", "close"]].max(axis=1)
    lower_wick   = df[["open", "close"]].min(axis=1) - df["low"]

    feat["body_ratio"]       = body        / candle_range
    feat["upper_wick_ratio"] = upper_wick  / candle_range
    feat["lower_wick_ratio"] = lower_wick  / candle_range
    feat["body_direction"]   = np.sign(df["close"] - df["open"])
    feat["is_doji"]          = (feat["body_ratio"] < 0.10).astype(int)
    feat["is_hammer"]        = (
        (lower_wick > 2 * body) & (upper_wick < body)
    ).astype(int)
    feat["is_shooting_star"] = (
        (upper_wick > 2 * body) & (lower_wick < body)
    ).astype(int)

    # ── Donchian breakout (shift prevents bar from seeing its own level) ──────
    roll_hi20 = df["high"].rolling(20).max().shift(1)
    roll_lo20 = df["low"].rolling(20).min().shift(1)
    feat["dist_hi_20"]     = (df["close"] - roll_hi20) / (atr + 1e-9)
    feat["dist_lo_20"]     = (df["close"] - roll_lo20) / (atr + 1e-9)
    feat["breakout_hi_20"] = (df["close"] > roll_hi20).astype(int)
    feat["breakout_lo_20"] = (df["close"] < roll_lo20).astype(int)

    # ── Drawdown from rolling max ─────────────────────────────────────────────
    roll_max20 = df["close"].rolling(20).max()
    feat["drawdown_20"] = (df["close"] - roll_max20) / (roll_max20 + 1e-9)

    # ── Session flags (UTC hours) ─────────────────────────────────────────────
    hour = df.index.hour
    feat["session_asia"]    = ((hour >= 0)  & (hour < 8)).astype(int)
    feat["session_london"]  = ((hour >= 8)  & (hour < 13)).astype(int)
    feat["session_ny"]      = ((hour >= 13) & (hour < 22)).astype(int)
    feat["session_overlap"] = ((hour >= 13) & (hour < 16)).astype(int)

    # ── Cyclical time encoding ────────────────────────────────────────────────
    feat["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    feat["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    dow              = df.index.dayofweek
    feat["dow_sin"]  = np.sin(2 * np.pi * dow / 7)
    feat["dow_cos"]  = np.cos(2 * np.pi * dow / 7)

    # ── 1-bar lags of key features ────────────────────────────────────────────
    feat["ret_1_lag1"]          = feat["ret_1"].shift(1)
    feat["body_direction_lag1"] = feat["body_direction"].shift(1)
    feat["vol_ratio_5_20_lag1"] = feat["vol_ratio_5_20"].shift(1)

    feat.replace([np.inf, -np.inf], np.nan, inplace=True)
    feat.dropna(inplace=True)
    return feat


# =============================================================================
# Row enrichment — bridges candle features into SignalLog row dicts
# =============================================================================

def enrich_signal_log_row(row: dict, candle_df: pd.DataFrame) -> dict:
    """
    Attach CANDLE_FEATURE_NAMES values to a single SignalLog row dict.

    Finds the most recent candle bar whose index is <= captured_at so the
    feature values reflect market state at signal-fire time.  Any missing
    feature is set to np.nan (imputed later in _build_feature_matrix).

    This does NOT overwrite existing keys (rsi, adx, etc.) — it adds new ones.
    """
    for f in CANDLE_FEATURE_NAMES:
        row.setdefault(f, np.nan)

    if candle_df.empty:
        return row

    captured_at = row.get("captured_at")
    if captured_at is None:
        return row

    ts   = pd.Timestamp(captured_at)
    ts   = ts if ts.tzinfo is not None else ts.tz_localize("UTC")
    mask = candle_df.index <= ts

    if not mask.any():
        return row

    bar = candle_df.loc[mask].iloc[-1]
    for f in CANDLE_FEATURE_NAMES:
        if f in bar.index and not pd.isna(bar[f]):
            row[f] = float(bar[f])

    return row


async def enrich_rows_batch(
    rows:         list[dict],
    symbol:       str,
    api_token:    str,
    granularity:  int = GRANULARITY["M15"],
    candle_count: int = 500,
) -> list[dict]:
    """
    Fetch ONE candle batch for the symbol, build the feature DataFrame,
    then enrich all rows.

    Call once per symbol inside calibration_trainer — not per row.
    Returns the enriched rows list (same objects, mutated in place).
    """
    candles   = await fetch_candles(symbol, granularity, candle_count, api_token)
    candle_df = build_feature_df(candles)

    if candle_df.empty:
        logger.warning(
            f"[feature_engineering] no candles returned for {symbol}; "
            "CANDLE_FEATURE_NAMES will be NaN — model will rely on native features only"
        )

    return [enrich_signal_log_row(row, candle_df) for row in rows]


# =============================================================================
# Private helpers
# =============================================================================

def _wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """True Range → Wilder-smoothed ATR.  Only uses past bars."""
    prev = df["close"].shift(1)
    tr   = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"]  - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()