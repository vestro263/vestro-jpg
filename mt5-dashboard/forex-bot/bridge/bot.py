"""
Bot entry point — Forex TSS + Crash 500 Nuclear Scalper.
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
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.mt5_connector import (
    connect, disconnect, get_account_info,
    get_ohlcv, get_open_positions, get_symbol_info, get_tick,
    BarStreamer,
)

from bridge.signal_bridge_py import get_signal
_ENGINE = "Python"

from bridge.risk_manager import RiskManager
from bridge.trade_executor import (
    send_order, move_to_breakeven, partial_close,
    TrailingStopManager,
)
from bridge.boom_crash import BoomCrashAnalyzer
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
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)
    fh = logging.handlers.RotatingFileHandler(
        log_cfg.get("log_file", "logs/bot.log"),
        maxBytes=log_cfg.get("max_bytes", 5_242_880),
        backupCount=log_cfg.get("backup_count", 3),
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

setup_logging(config)
logger = logging.getLogger("bot")
logger.info(f"Signal engine: {_ENGINE}")

# ── State ──────────────────────────────────────────────────────────────────
risk_manager        = RiskManager(config)
_signal_cache:      Dict[str, dict] = {}
_trailing_managers: Dict[int, TrailingStopManager] = {}
_tp1_closed:        set = set()
_bc_analyzers:      Dict[str, BoomCrashAnalyzer] = {}
_bc_5m_cache:       Dict[str, dict] = {}

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
            logger.error(f"Telegram failed: {e}")


# ── Position management ────────────────────────────────────────────────────
def manage_open_positions(symbol: str, df):
    positions = get_open_positions(symbol)
    if not positions:
        return
    cfg_risk    = config.get("risk", {})
    atr         = df["close"].diff().abs().rolling(14).mean().iloc[-1]
    current     = df["close"].iloc[-1]
    for pos in positions:
        if pos.get("magic") != 20250101:
            continue
        ticket      = pos["ticket"]
        direction   = pos["type"]
        open_px     = pos["open_price"]
        sl          = pos["sl"]
        tp          = pos["tp"]
        volume      = pos["volume"]
        sl_dist     = abs(open_px - sl)
        tp1_dist    = sl_dist * cfg_risk.get("tp1_rr", 1.5)
        profit_dist = (current - open_px) if direction == "buy" else (open_px - current)
        if ticket not in _tp1_closed and profit_dist >= tp1_dist * 0.98:
            close_vol = round(volume * 0.5, 2)
            try:
                partial_close(ticket, close_vol, symbol, direction)
                _tp1_closed.add(ticket)
                move_to_breakeven(ticket, open_px, tp, buffer_pts=0.00002)
                logger.info(f"TP1 taken on {ticket}: {close_vol} lots")
                broadcast_sync({"type": "tp1_hit", "ticket": ticket,
                                "symbol": symbol, "closed_volume": close_vol})
            except Exception as e:
                logger.error(f"TP1 error: {e}")
        if ticket in _tp1_closed:
            if ticket not in _trailing_managers:
                _trailing_managers[ticket] = TrailingStopManager(
                    ticket, symbol, direction, sl, atr_multiplier=2.0)
            _trailing_managers[ticket].update(current, atr, tp)


# ── Forex bar callback ─────────────────────────────────────────────────────
def on_new_bar(symbol: str, df):
    logger.info(f"New bar: {symbol} | {df['time'].iloc[-1]}")
    try:
        sig = get_signal(df)
        _signal_cache[symbol] = {**sig, "symbol": symbol,
                                  "timestamp": str(df["time"].iloc[-1])}
        account   = get_account_info()
        balance   = account.get("balance", 0.0)
        positions = get_open_positions()
        sym_info  = get_symbol_info(symbol)
        point     = sym_info.get("point", 0.00001)
        pip_value = sym_info.get("trade_tick_value", 1.0) * \
                    (sym_info.get("point", 0.00001) /
                     sym_info.get("trade_tick_size", 0.00001))
        approved, trade_info = risk_manager.approve_trade(
            sig, balance, positions, symbol, point, pip_value, sym_info)
        event = {
            "type": "signal", "symbol": symbol, "signal": sig,
            "approved": approved,
            "account": {"balance": balance, "equity": account.get("equity", 0)},
            "reason": trade_info.get("reason", ""),
        }
        if approved and sig["direction"] != 0:
            tick      = get_tick(symbol)
            direction = "buy" if sig["direction"] == 1 else "sell"
            entry     = tick["ask"] if direction == "buy" else tick["bid"]
            sl, tp1, tp2 = risk_manager.calc_sl_tp(
                sig["direction"], entry, sig["atr"], point)
            try:
                result = send_order(symbol, direction, trade_info["lot_size"],
                                    sl_price=sl, tp_price=tp2,
                                    comment=f"BOT TSS={sig['tss_score']}")
                log_trade(ticket=result["ticket"], symbol=symbol,
                          direction=direction, lot_size=trade_info["lot_size"],
                          entry=entry, sl=sl, tp1=tp1, tp2=tp2,
                          tss_score=sig["tss_score"],
                          checklist=sig["checklist_score"],
                          reason=sig["reason"], atr_zone=sig["atr_zone"])
                event["trade"] = {"ticket": result["ticket"],
                                  "direction": direction,
                                  "lot_size": trade_info["lot_size"],
                                  "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2}
                send_telegram(f"TRADE {symbol} {direction.upper()}\n"
                              f"Lot:{trade_info['lot_size']} TSS:{sig['tss_score']}")
            except Exception as e:
                logger.error(f"Order error: {e}")
                event["order_error"] = str(e)
        manage_open_positions(symbol, df)
        broadcast_sync(event)
    except Exception as e:
        logger.error(f"on_new_bar error {symbol}: {e}", exc_info=True)
        broadcast_sync({"type": "error", "symbol": symbol, "error": str(e)})


# ── Boom/Crash bar callback ────────────────────────────────────────────────
def on_new_bar_bc(symbol: str, df_1m):
    try:
        analyzer = _bc_analyzers.get(symbol)
        if not analyzer:
            return
        now    = time.time()
        cached = _bc_5m_cache.get(symbol, {})
        if not cached or now - cached.get("ts", 0) > 60:
            try:
                df_5m = get_ohlcv(symbol, "M5", 100)
                _bc_5m_cache[symbol] = {"df": df_5m, "ts": now}
            except Exception as e:
                logger.error(f"5M fetch failed {symbol}: {e}")
                return
        else:
            df_5m = cached["df"]
        result = analyzer.evaluate(df_1m, df_5m)
        prev   = _signal_cache.get(symbol, {})
        if (result.get("approved") == prev.get("approved") and
                result.get("direction") == prev.get("direction") and
                not result.get("approved")):
            return
        _signal_cache[symbol] = {**result, "timestamp": str(df_1m["time"].iloc[-1])}
        event = {
            "type": "signal", "symbol": symbol, "signal": result,
            "approved": result.get("approved", False),
            "account": get_account_info(), "reason": result.get("reason", ""),
        }
        if result.get("approved") and result.get("direction", 0) != 0:
            direction = "buy" if result["direction"] == 1 else "sell"
            lot_size  = config.get("boom_crash", {}).get("lot_size", 0.2)
            try:
                r = send_order(symbol, direction, lot_size,
                               sl_price=result["sl"], tp_price=result["tp"],
                               comment="BC spike")
                log_trade(ticket=r["ticket"], symbol=symbol, direction=direction,
                          lot_size=lot_size, entry=result["entry"],
                          sl=result["sl"], tp1=result["tp"], tp2=result["tp"],
                          tss_score=0, checklist=0, reason=result["reason"],
                          atr_zone="normal")
                event["trade"] = {"ticket": r["ticket"], "direction": direction,
                                  "lot_size": lot_size, "entry": result["entry"],
                                  "sl": result["sl"], "tp": result["tp"]}
            except Exception as e:
                logger.error(f"BC order error {symbol}: {e}")
                event["order_error"] = str(e)
        broadcast_sync(event)
    except Exception as e:
        logger.error(f"on_new_bar_bc error {symbol}: {e}", exc_info=True)


# ── Shutdown ───────────────────────────────────────────────────────────────
streamers     = []
crash_scalper = None

def shutdown(sig=None, frame=None):
    logger.info("Shutting down...")
    if crash_scalper:
        crash_scalper.stop()
    for s in streamers:
        s.stop()
    disconnect()
    sys.exit(0)

signal.signal(signal.SIGINT,  shutdown)
signal.signal(signal.SIGTERM, shutdown)


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    global crash_scalper

    logger.info("=" * 60)
    logger.info("  VESTRO BOT — Starting")
    logger.info("=" * 60)

    init_db()

    account = connect(
        login    = int(os.getenv("MT5_LOGIN",    config["mt5"]["login"])),
        password = os.getenv("MT5_PASSWORD",     config["mt5"]["password"]),
        server   = os.getenv("MT5_SERVER",       config["mt5"]["server"]),
    )
    logger.info(f"MT5: {account.get('name')} | ${account.get('balance')} {account.get('currency')}")

    # API server
    import uvicorn
    api_cfg    = config.get("api", {})
    api_thread = threading.Thread(
        target=lambda: uvicorn.run(
            "bridge.api_server:app",
            host=api_cfg.get("host", "0.0.0.0"),
            port=api_cfg.get("port", 8000),
            log_level="warning",
        ),
        daemon=True, name="APIServer",
    )
    api_thread.start()
    logger.info(f"API server on port {api_cfg.get('port', 8000)}")

    # Forex streamers
    tf = config["trading"]["primary_timeframe"]
    for symbol in config["trading"]["symbols"]:
        s = BarStreamer(symbol, tf, on_new_bar)
        s.start()
        streamers.append(s)
    logger.info(f"Forex: {len(config['trading']['symbols'])} symbols on {tf}")

    # Boom/Crash streamers
    bc_cfg     = config.get("boom_crash", {})
    bc_enabled = bc_cfg.get("enabled", False)
    bc_symbols = bc_cfg.get("symbols", [])
    if bc_enabled and bc_symbols:
        for symbol in bc_symbols:
            _bc_analyzers[symbol] = BoomCrashAnalyzer(symbol, config)
            s = BarStreamer(symbol, "M1", on_new_bar_bc, poll_interval=1.0)
            s.start()
            streamers.append(s)
        logger.info(f"Boom/Crash: {len(bc_symbols)} symbols on M1")
    else:
        logger.info("Boom/Crash: disabled")

    # Crash 500 Nuclear Scalper
    from bridge.crash500_scalper import Crash500Scalper
    crash_scalper = Crash500Scalper(
        config         = config,
        broadcast_fn   = broadcast_sync,
        get_account_fn = get_account_info,
        get_ohlcv_fn   = get_ohlcv,
        send_order_fn  = send_order,
        log_trade_fn   = log_trade,
        get_tick_fn    = get_tick,
    )
    crash_scalper.start()
    logger.info("Crash500 NUCLEAR scalper active")

    send_telegram("Vestro Bot STARTED")

    # Heartbeat
    while True:
        time.sleep(30)
        try:
            account = get_account_info()
            broadcast_sync({"type": "heartbeat", "account": account,
                            "timestamp": time.time()})
        except Exception as e:
            logger.warning(f"Heartbeat error: {e}")


if __name__ == "__main__":
    main()