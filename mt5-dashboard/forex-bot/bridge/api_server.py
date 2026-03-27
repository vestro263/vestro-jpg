"""
bridge/api_server.py
────────────────────
FastAPI app for the Vestro forex bot dashboard.
  REST : /health  /account  /positions  /bot/start  /bot/stop  /bot/status
  WS   : ws://<host>:<port>/ws
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Vestro Bot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── State ─────────────────────────────────────────────────────────────────

_loop: Optional[asyncio.AbstractEventLoop] = None
_bot_running: bool = False

# ── WebSocket manager ─────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info("WS client connected (total=%d)", len(self.active))

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        logger.info("WS client disconnected (total=%d)", len(self.active))

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(json.dumps(data, default=str))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


_manager = ConnectionManager()

# ── Public broadcast helpers ──────────────────────────────────────────────

async def broadcast(data: dict):
    """Async broadcast — use with await from async code."""
    await _manager.broadcast(data)


def broadcast_sync(data: dict):
    """Thread-safe broadcast — call from any background thread."""
    if _loop is None or not _loop.is_running():
        return  # API not ready yet, drop silently
    asyncio.run_coroutine_threadsafe(_manager.broadcast(data), _loop)

# ── Startup ───────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup():
    global _loop
    _loop = asyncio.get_event_loop()
    asyncio.create_task(_heartbeat_loop())
    logger.info("API server ready — event loop captured")


async def _heartbeat_loop():
    while True:
        await asyncio.sleep(5)
        try:
            acct = _get_account_cached()
            await _manager.broadcast({
                "type":        "heartbeat",
                "account":     acct,
                "bot_running": _bot_running,
            })
        except Exception as e:
            logger.warning("Heartbeat error: %s", e)

# ── Account cache ─────────────────────────────────────────────────────────

_account_cache: dict = {"data": {}, "ts": 0.0}
_ACCOUNT_TTL = 5.0


def _get_account_cached() -> dict:
    now = time.time()
    if now - _account_cache["ts"] < _ACCOUNT_TTL:
        return _account_cache["data"]
    try:
        from bridge.mt5_connector import get_account_info
        data = get_account_info() or {}
    except Exception:
        data = {"balance": 0, "equity": 0, "currency": "USD",
                "profit": 0, "margin_free": 0, "name": "—", "leverage": 0}
    _account_cache["data"] = data
    _account_cache["ts"]   = now
    return data

# ── WebSocket endpoint ────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await _manager.connect(ws)

    # Replay cached signals immediately so UI isn't blank on connect
    try:
        from bridge import bot
        if hasattr(bot, "_signal_cache") and bot._signal_cache:
            for sym, sig in bot._signal_cache.items():
                await ws.send_text(json.dumps({
                    "type":     "signal",
                    "source":   sig.get("source", "cached"),
                    "symbol":   sym,
                    "signal":   sig,
                    "approved": sig.get("approved", False),
                    "reason":   sig.get("reason", ""),
                }, default=str))
    except Exception as e:
        logger.warning("Cache replay error: %s", e)

    try:
        while True:
            await ws.receive_text()   # keep alive; ignore incoming
    except WebSocketDisconnect:
        _manager.disconnect(ws)

# ── REST endpoints ────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "vestro-bot",
            "ws_clients": len(_manager.active)}


@app.get("/account")
def account():
    return _get_account_cached()


@app.get("/positions")
def positions():
    try:
        from bridge.mt5_connector import get_open_positions
        result = get_open_positions()
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.error("positions error: %s", e)
        return []


@app.post("/bot/start")
def start_bot():
    global _bot_running
    _bot_running = True
    return {"status": "started"}


@app.post("/bot/stop")
def stop_bot():
    global _bot_running
    _bot_running = False
    return {"status": "stopped"}


@app.get("/bot/status")
def bot_status():
    return {"running": _bot_running}


@app.get("/journal")
def journal(limit: int = 50):
    try:
        from db.journal import get_trades
        return get_trades(limit=limit)
    except Exception as e:
        logger.error("journal error: %s", e)
        return []


@app.get("/stats")
def stats(days: int = 30):
    try:
        from db.journal import get_stats
        return get_stats(days=days)
    except Exception as e:
        logger.error("stats error: %s", e)
        return {}