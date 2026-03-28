"""
MT5 Connector — LOCAL bridge to send MT5 data to remote API (Render)
"""

import time
import logging
import threading
import os
import requests
from dotenv import load_dotenv

# ✅ Load env from parent folder (forex-bot/.env)
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logging.warning("MetaTrader5 not installed. Running in DEMO mode.")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# 🔥 Your deployed API
API_URL = "https://vestro-jpg.onrender.com/api"


# ─────────────────────────────────────────────────────────────
# CONNECTION
# ─────────────────────────────────────────────────────────────

def connect(login: int, password: str, server: str):
    if not MT5_AVAILABLE:
        return {"status": "demo"}

    for _ in range(5):
        mt5.shutdown()
        time.sleep(2)

        if mt5.initialize(login=login, password=password, server=server):
            info = mt5.account_info()
            if info:
                logger.info(f"✅ MT5 Connected: {info.login}")
                return info._asdict()

        logger.error(f"❌ MT5 retry: {mt5.last_error()}")

    raise RuntimeError("MT5 connection failed")


def disconnect():
    if MT5_AVAILABLE:
        mt5.shutdown()
        logger.info("MT5 disconnected")


# ─────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────

def get_account_info() -> dict:
    if not MT5_AVAILABLE:
        return {
            "balance": 10000,
            "equity": 10000,
            "profit": 0,
            "currency": "USD"
        }

    info = mt5.account_info()
    if info is None:
        return {}

    return {
        "login": info.login,
        "balance": info.balance,
        "equity": info.equity,
        "profit": info.profit,
        "margin": info.margin,
        "margin_free": info.margin_free,
        "currency": info.currency,
        "leverage": info.leverage,
        "name": info.name
    }


def get_open_positions() -> list:
    if not MT5_AVAILABLE:
        return []

    positions = mt5.positions_get()
    if positions is None:
        return []

    return [
        {
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "buy" if p.type == 0 else "sell",
            "volume": p.volume,
            "open_price": p.price_open,
            "current": p.price_current,
            "profit": p.profit,
            "sl": p.sl,
            "tp": p.tp,
        }
        for p in positions
    ]


# ─────────────────────────────────────────────────────────────
# PUSH TO API
# ─────────────────────────────────────────────────────────────

def push_account():
    try:
        data = get_account_info()
        requests.post(f"{API_URL}/account/update", json=data, timeout=5)
        logger.info("📤 Account pushed")
    except Exception as e:
        logger.error(f"Account push failed: {e}")


def push_positions():
    try:
        data = get_open_positions()
        requests.post(f"{API_URL}/positions/update", json=data, timeout=5)
        logger.info("📤 Positions pushed")
    except Exception as e:
        logger.error(f"Positions push failed: {e}")


# ─────────────────────────────────────────────────────────────
# BACKGROUND LOOP
# ─────────────────────────────────────────────────────────────

def start_push_loop(interval=5):
    def loop():
        while True:
            push_account()
            push_positions()
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    logger.info("🚀 MT5 push loop started")


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        # ✅ Read credentials from .env
        login = int(os.getenv("MT5_LOGIN"))
        password = os.getenv("MT5_PASSWORD")
        server = os.getenv("MT5_SERVER")

        print("🔐 LOGIN:", login)
        print("🌐 SERVER:", server)

        connect(login=login, password=password, server=server)
        start_push_loop()

        while True:
            time.sleep(1)

    except Exception as e:
        logger.error(f"🔥 Fatal error: {e}")