"""
MT5 Connector — handles all MetaTrader 5 communication.
Provides connection management, OHLCV fetching, and real-time bar streaming.
"""

import os
import time
import logging
import threading
from typing import Callable, Optional
import pandas as pd

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logging.warning("MetaTrader5 package not installed. Running in DEMO mode.")

logger = logging.getLogger(__name__)

# ── Timeframe mapping ──────────────────────────────────────────────────────
TF_MAP = {
    "M1":  1,
    "M5":  5,
    "M15": 15,
    "M30": 30,
    "H1":  16385,
    "H4":  16388,
    "D1":  16408,
    "W1":  32769,
    "MN1": 49153,
}

def get_tf(name: str):
    """Return MT5 timeframe constant from string name."""
    if not MT5_AVAILABLE:
        return TF_MAP.get(name, 16385)
    tf_map = {
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,
        "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
        "W1":  mt5.TIMEFRAME_W1,
        "MN1": mt5.TIMEFRAME_MN1,
    }
    return tf_map.get(name.upper(), mt5.TIMEFRAME_H1)


# ── Connection ─────────────────────────────────────────────────────────────
def connect(login: int = None, password: str = None,
            server: str = None) -> dict:
    """
    Initialize MT5 connection.
    Falls back to environment variables if params not provided.
    Returns account info dict.
    """
    if not MT5_AVAILABLE:
        logger.warning("MT5 not available — returning mock account.")
        return {"name": "DEMO", "balance": 10000.0,
                "equity": 10000.0, "profit": 0.0, "currency": "USD"}

    login    = login    or int(os.getenv("MT5_LOGIN", "0"))
    password = password or os.getenv("MT5_PASSWORD", "")
    server   = server   or os.getenv("MT5_SERVER", "")

    if not mt5.initialize(login=login, password=password, server=server):
        raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")

    info = mt5.account_info()
    if info is None:
        raise RuntimeError(f"Cannot get account info: {mt5.last_error()}")

    logger.info(
        f"MT5 connected | Account: {info.name} | "
        f"Balance: {info.balance} {info.currency} | "
        f"Server: {info.server}"
    )
    return info._asdict()


def disconnect():
    """Cleanly shut down MT5 connection."""
    if MT5_AVAILABLE:
        mt5.shutdown()
        logger.info("MT5 disconnected.")


def get_account_info() -> dict:
    """Return current account balance, equity, profit."""
    if not MT5_AVAILABLE:
        return {"balance": 10000.0, "equity": 10000.0,
                "profit": 0.0, "currency": "USD",
                "name": "DEMO", "login": 0}
    info = mt5.account_info()
    if info is None:
        return {}
    return {
        "login":    info.login,
        "name":     info.name,
        "server":   info.server,
        "balance":  info.balance,
        "equity":   info.equity,
        "profit":   info.profit,
        "margin":   info.margin,
        "margin_free": info.margin_free,
        "currency": info.currency,
        "leverage": info.leverage,
    }


# ── OHLCV fetching ─────────────────────────────────────────────────────────
def get_ohlcv(symbol: str, timeframe_str: str, count: int = 500) -> pd.DataFrame:
    """
    Fetch OHLCV bars from MT5.
    Returns DataFrame with columns: time, open, high, low, close, volume.
    """
    if not MT5_AVAILABLE:
        return _mock_ohlcv(symbol, count)

    tf = get_tf(timeframe_str)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        raise ValueError(
            f"No data for {symbol}/{timeframe_str}: {mt5.last_error()}"
        )

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.rename(columns={"tick_volume": "volume"})
    df = df[["time", "open", "high", "low", "close", "volume"]].copy()
    df = df.sort_values("time").reset_index(drop=True)
    return df


def get_symbol_info(symbol: str) -> dict:
    """Return symbol metadata: point, digits, pip_value etc."""
    if not MT5_AVAILABLE:
        return {"point": 0.00001, "digits": 5,
                "trade_tick_value": 1.0, "trade_tick_size": 0.00001,
                "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01}
    info = mt5.symbol_info(symbol)
    if info is None:
        raise ValueError(f"Symbol not found: {symbol}")
    return {
        "point":            info.point,
        "digits":           info.digits,
        "trade_tick_value": info.trade_tick_value,
        "trade_tick_size":  info.trade_tick_size,
        "volume_min":       info.volume_min,
        "volume_max":       info.volume_max,
        "volume_step":      info.volume_step,
        "spread":           info.spread,
    }


def get_tick(symbol: str) -> dict:
    """Return latest bid/ask tick."""
    if not MT5_AVAILABLE:
        return {"bid": 1.10000, "ask": 1.10002, "time": time.time()}
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise ValueError(f"No tick for {symbol}: {mt5.last_error()}")
    return {"bid": tick.bid, "ask": tick.ask, "time": tick.time}


def get_open_positions(symbol: str = None) -> list:
    """Return list of open positions, optionally filtered by symbol."""
    if not MT5_AVAILABLE:
        return []
    positions = mt5.positions_get(symbol=symbol) if symbol \
                else mt5.positions_get()
    if positions is None:
        return []
    result = []
    for p in positions:
        result.append({
            "ticket":      p.ticket,
            "symbol":      p.symbol,
            "type":        "buy" if p.type == 0 else "sell",
            "volume":      p.volume,
            "open_price":  p.price_open,
            "current":     p.price_current,
            "sl":          p.sl,
            "tp":          p.tp,
            "profit":      p.profit,
            "swap":        p.swap,
            "commission":  p.commission,
            "magic":       p.magic,
            "comment":     p.comment,
            "open_time":   p.time,
        })
    return result


def get_history_deals(days_back: int = 30) -> list:
    """Return closed deals from the past N days."""
    if not MT5_AVAILABLE:
        return []
    from datetime import datetime, timedelta
    date_from = datetime.now() - timedelta(days=days_back)
    date_to   = datetime.now()
    deals = mt5.history_deals_get(date_from, date_to)
    if deals is None:
        return []
    return [
        {
            "ticket":   d.ticket,
            "order":    d.order,
            "symbol":   d.symbol,
            "type":     d.type,
            "volume":   d.volume,
            "price":    d.price,
            "profit":   d.profit,
            "swap":     d.swap,
            "time":     d.time,
            "comment":  d.comment,
        }
        for d in deals
    ]


# ── Real-time bar streamer ─────────────────────────────────────────────────
class BarStreamer:
    """
    Polls MT5 every `poll_interval` seconds.
    Calls `on_new_bar(symbol, df)` whenever a new bar closes.
    """

    def __init__(self, symbol: str, timeframe_str: str,
                 on_new_bar: Callable, poll_interval: float = 1.0):
        self.symbol        = symbol
        self.timeframe_str = timeframe_str
        self.callback      = on_new_bar
        self.poll_interval = poll_interval
        self._last_bar_time = None
        self._running       = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True,
            name=f"BarStreamer-{self.symbol}-{self.timeframe_str}"
        )
        self._thread.start()
        logger.info(f"BarStreamer started: {self.symbol} {self.timeframe_str}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info(f"BarStreamer stopped: {self.symbol} {self.timeframe_str}")

    def _loop(self):
        while self._running:
            try:
                df = get_ohlcv(self.symbol, self.timeframe_str, 500)
                if df is not None and len(df) > 0:
                    latest_time = df["time"].iloc[-1]
                    if latest_time != self._last_bar_time:
                        if self._last_bar_time is not None:
                            # New bar just closed — trigger callback
                            self.callback(self.symbol, df)
                        self._last_bar_time = latest_time
            except Exception as e:
                logger.error(f"BarStreamer {self.symbol} error: {e}")
            time.sleep(self.poll_interval)


# ── Mock data for offline testing ──────────────────────────────────────────
def _mock_ohlcv(symbol: str, count: int = 500) -> pd.DataFrame:
    """Generate synthetic OHLCV data for offline testing."""
    import numpy as np
    np.random.seed(hash(symbol) % 2**32)
    dates  = pd.date_range(end=pd.Timestamp.now(), periods=count, freq="h")
    close  = 1.1000 + np.cumsum(np.random.randn(count) * 0.0005)
    spread = 0.0002
    high   = close + np.abs(np.random.randn(count) * 0.0010)
    low    = close - np.abs(np.random.randn(count) * 0.0010)
    open_  = close + np.random.randn(count) * 0.0003
    volume = np.random.randint(1000, 5000, count).astype(float)
    return pd.DataFrame({
        "time":   dates,
        "open":   open_,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": volume,
    })