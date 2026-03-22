"""
Bot entry point — wires together MT5, C++ signals, risk manager,
executor, journal, and API server.
"""

import asyncio
import logging
import logging.handlers
import os
import signal
import sys
import time
import threading
from typing import Dict

import yaml

# ── Bootstrap path so bridge imports work ─────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.mt5_connector import (
    connect, disconnect, get_account_info,
    get_ohlcv, get_open_positions, get_symbol_info, get_tick,
    BarStreamer,
)
from bridge.signal_bridge import get_signal
from bridge.risk_manager   import RiskManager
from bridge.trade_executor import (
    send_order, move_to_breakeven, partial_close,
    TrailingStopManager,
)
from db.journal import init_db, log_trade, update_trade_exit
from bridge.api_server import broadcast_sync

# ── Config ─────────────────────────────────────────────────────────────────
def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)

config = load_config()

# ── Logging ────────────────────────────────────────────────────────────────
def setup_logging(cfg: dict):
    log_cfg = cfg.get("logging", {})
    os.makedirs("logs", exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_cfg.get("level", "INFO")))
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)
    # Rotating file
    fh = logging.handlers.RotatingFileHandler(
        log_cfg.get("log_file", "logs/bot.log"),
        maxBytes=log_cfg.get("max_bytes", 5_242_880),
        backupCount=log_cfg.get("backup_count", 3),
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

setup_logging(config)
logger = logging.getLogger("bot")

# ── State ──────────────────────────────────────────────────────────────────
risk_manager = RiskManager(config)
_signal_cache: Dict[str, dict] = {}          # latest signal per symbol
_trailing_managers: Dict[int, TrailingStopManager] = {}  # ticket → trail mgr
_tp1_closed: set = set()                     # tickets where TP1 already taken

def get_cached_signal(symbol: str) -> dict:
    return _signal_cache.get(symbol)


# ── Alerts ─────────────────────────────────────────────────────────────────
def send_telegram(msg: str):
    cfg = config.get("alerts", {})
    if not cfg.get("telegram_enabled"):
        return
    import requests
    token   = os.getenv("TG_BOT_TOKEN", cfg.get("telegram_token", ""))
    chat_id = os.getenv("TG_CHAT_ID",   cfg.get("telegram_chat_id", ""))
    if token and chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg}, timeout=5,
            )
        except Exception as e:
            logger.error(f"Telegram alert failed: {e}")


# ── Position management on each bar ───────────────────────────────────────
def manage_open_positions(symbol: str, df):
    """
    Called on every new bar. Checks open positions for:
    - TP1 partial close (50% at 1.5R)
    - Break-even move
    - Trailing stop update
    """
    positions = get_open_positions(symbol)
    if not positions:
        return

    cfg_risk = config.get("risk", {})
    atr      = df["close"].diff().abs().rolling(14).mean().iloc[-1]
    current  = df["close"].iloc[-1]

    for pos in positions:
        if pos.get("magic") != 20250101:
            continue   # not our bot's trade

        ticket    = pos["ticket"]
        direction = pos["type"]   # "buy" or "sell"
        open_px   = pos["open_price"]
        sl        = pos["sl"]
        tp        = pos["tp"]
        volume    = pos["volume"]

        sl_dist = abs(open_px - sl)
        tp1_dist = sl_dist * cfg_risk.get("tp1_rr", 1.5)
        profit_dist = (current - open_px) if direction == "buy" \
                      else (open_px - current)

        # TP1 partial close — take 50% off at 1.5R
        if ticket not in _tp1_closed and profit_dist >= tp1_dist * 0.98:
            close_vol = round(volume * 0.5, 2)
            try:
                partial_close(ticket, close_vol, symbol, direction)
                _tp1_closed.add(ticket)
                # Move SL to break-even
                move_to_breakeven(ticket, open_px, tp, buffer_pts=0.00002)
                logger.info(f"TP1 taken on ticket {ticket}: closed {close_vol} lots")
                broadcast_sync({
                    "type": "tp1_hit",
                    "ticket": ticket,
                    "symbol": symbol,
                    "closed_volume": close_vol,
                })
            except Exception as e:
                logger.error(f"TP1 partial close error: {e}")

        # Update trailing stop after TP1
        if ticket in _tp1_closed:
            if ticket not in _trailing_managers:
                _trailing_managers[ticket] = TrailingStopManager(
                    ticket, symbol, direction, sl, atr_multiplier=2.0
                )
            _trailing_managers[ticket].update(current, atr, tp)


# ── New bar callback ───────────────────────────────────────────────────────
def on_new_bar(symbol: str, df):
    logger.info(f"New bar: {symbol} | {df['time'].iloc[-1]}")

    try:
        # 1. Get C++ signal
        signal = get_signal(df)
        _signal_cache[symbol] = {**signal, "symbol": symbol,
                                  "timestamp": str(df["time"].iloc[-1])}

        # 2. Account state
        account   = get_account_info()
        balance   = account.get("balance", 0.0)
        positions = get_open_positions()
        sym_info  = get_symbol_info(symbol)

        point     = sym_info.get("point", 0.00001)
        # pip_value: value per pip per 1 lot in account currency
        pip_value = sym_info.get("trade_tick_value", 1.0) * \
                    (sym_info.get("point", 0.00001) /
                     sym_info.get("trade_tick_size", 0.00001))

        # 3. Risk approval
        approved, trade_info = risk_manager.approve_trade(
            signal, balance, positions, symbol,
            point, pip_value, sym_info
        )

        # 4. Execute if approved
        event = {
            "type":     "signal",
            "symbol":   symbol,
            "signal":   signal,
            "approved": approved,
            "account":  {"balance": balance, "equity": account.get("equity", 0)},
            "reason":   trade_info.get("reason", ""),
        }

        if approved and signal["direction"] != 0:
            tick      = get_tick(symbol)
            direction = "buy" if signal["direction"] == 1 else "sell"
            entry     = tick["ask"] if direction == "buy" else tick["bid"]

            # Recalculate SL/TP from actual entry price
            sl, tp1, tp2 = risk_manager.calc_sl_tp(
                signal["direction"], entry,
                signal["atr"], point
            )

            try:
                result = send_order(
                    symbol, direction,
                    trade_info["lot_size"],
                    sl_price=sl,
                    tp_price=tp2,   # TP2 is the hard target
                    comment=f"BOT TSS={signal['tss_score']}",
                )
                # Store TP1 target for position manager
                log_trade(
                    ticket    = result["ticket"],
                    symbol    = symbol,
                    direction = direction,
                    lot_size  = trade_info["lot_size"],
                    entry     = entry,
                    sl        = sl,
                    tp1       = tp1,
                    tp2       = tp2,
                    tss_score = signal["tss_score"],
                    checklist = signal["checklist_score"],
                    reason    = signal["reason"],
                    atr_zone  = signal["atr_zone"],
                )
                event["trade"] = {
                    "ticket":    result["ticket"],
                    "direction": direction,
                    "lot_size":  trade_info["lot_size"],
                    "entry":     entry,
                    "sl":        sl,
                    "tp1":       tp1,
                    "tp2":       tp2,
                }
                send_telegram(
                    f"TRADE OPEN\n{symbol} {direction.upper()}\n"
                    f"Lot: {trade_info['lot_size']}\nSL: {sl}\nTP2: {tp2}\n"
                    f"TSS: {signal['tss_score']}"
                )
            except Exception as e:
                logger.error(f"Order execution error: {e}")
                event["order_error"] = str(e)

        # 5. Manage existing positions
        manage_open_positions(symbol, df)

        # 6. Broadcast to dashboard
        broadcast_sync(event)

    except Exception as e:
        logger.error(f"on_new_bar error for {symbol}: {e}", exc_info=True)
        broadcast_sync({"type": "error", "symbol": symbol, "error": str(e)})


# ── Graceful shutdown ──────────────────────────────────────────────────────
streamers = []

def shutdown(sig=None, frame=None):
    logger.info("Shutting down bot...")
    for s in streamers:
        s.stop()
    disconnect()
    sys.exit(0)

signal.signal(signal.SIGINT,  shutdown)
signal.signal(signal.SIGTERM, shutdown)


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    logger.info("=" * 60)
    logger.info("  FOREX BOT — Starting")
    logger.info("=" * 60)

    # Init database
    init_db()

    # Connect to MT5
    account = connect(
        login    = int(os.getenv("MT5_LOGIN",    config["mt5"]["login"])),
        password = os.getenv("MT5_PASSWORD",     config["mt5"]["password"]),
        server   = os.getenv("MT5_SERVER",       config["mt5"]["server"]),
    )
    logger.info(f"Account: {account}")

    # Start API server in background thread
    import uvicorn
    api_cfg = config.get("api", {})
    api_thread = threading.Thread(
        target=lambda: uvicorn.run(
            "bridge.api_server:app",
            host=api_cfg.get("host", "0.0.0.0"),
            port=api_cfg.get("port", 8000),
            log_level="warning",
        ),
        daemon=True,
        name="APIServer",
    )
    api_thread.start()
    logger.info(f"API server started on port {api_cfg.get('port', 8000)}")

    # Start bar streamers for each symbol
    tf = config["trading"]["primary_timeframe"]
    for symbol in config["trading"]["symbols"]:
        s = BarStreamer(symbol, tf, on_new_bar)
        s.start()
        streamers.append(s)

    logger.info(f"Streaming {len(streamers)} symbols on {tf}")
    send_telegram("Forex Bot STARTED")

    # Keep alive
    while True:
        time.sleep(30)
        # Periodic account broadcast
        try:
            account = get_account_info()
            broadcast_sync({"type": "heartbeat", "account": account,
                            "timestamp": time.time()})
        except Exception as e:
            logger.warning(f"Heartbeat error: {e}")


if __name__ == "__main__":
    main()