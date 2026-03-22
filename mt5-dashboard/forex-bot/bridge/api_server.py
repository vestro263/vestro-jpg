import asyncio
import json
import logging
import time
import os
import threading
from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

app = FastAPI(title="Forex Bot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Main event loop — stored at startup, used by all threads ───────────────
_main_loop: asyncio.AbstractEventLoop = None


@app.on_event("startup")
async def _store_loop():
    global _main_loop
    _main_loop = asyncio.get_event_loop()
    logger.info("Event loop stored for cross-thread broadcasting")


# ── Connection manager ─────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info(f"WS connected. Total: {len(self.active)}")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        logger.info(f"WS disconnected. Total: {len(self.active)}")

    async def broadcast(self, data: dict):
        dead = []
        text = json.dumps(data, default=str)
        for ws in self.active:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

manager = ConnectionManager()


async def broadcast_event(event: dict):
    await manager.broadcast(event)


def broadcast_sync(event: dict):
    """
    Thread-safe broadcast — works from ANY thread.
    The Crash500Scalper, BarStreamers, and heartbeat loop
    all call this safely without needing their own event loop.
    """
    global _main_loop
    try:
        if _main_loop is not None and _main_loop.is_running():
            asyncio.run_coroutine_threadsafe(broadcast_event(event), _main_loop)
        # else: loop not ready yet — silently skip
    except Exception as e:
        logger.error(f"Broadcast error: {e}")


# ── WebSocket endpoint ─────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    def safe_account():
        try:
            from bridge.mt5_connector import get_account_info
            result = get_account_info()
            return result if result else {}
        except Exception:
            return {"balance": 0, "equity": 0, "currency": "USD",
                    "name": "—", "leverage": 0, "margin_free": 0}

    try:
        await websocket.send_text(json.dumps({
            "type":      "heartbeat",
            "account":   safe_account(),
            "timestamp": time.time(),
        }, default=str))
    except Exception:
        manager.disconnect(websocket)
        return

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=8.0)
            except asyncio.TimeoutError:
                pass
            await websocket.send_text(json.dumps({
                "type":      "heartbeat",
                "account":   safe_account(),
                "timestamp": time.time(),
            }, default=str))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WS session ended: {e}")
        manager.disconnect(websocket)


# ── News cache (15 min TTL) ────────────────────────────────────────────────
_news_cache: dict = {"data": [], "ts": 0.0}


# ── REST endpoints ─────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "timestamp": time.time()}


@app.get("/account")
def account_info():
    try:
        from bridge.mt5_connector import get_account_info
        return get_account_info()
    except Exception as e:
        return {"error": str(e), "balance": 0, "equity": 0}


@app.get("/positions")
def open_positions():
    try:
        from bridge.mt5_connector import get_open_positions
        return get_open_positions()
    except Exception as e:
        return {"error": str(e)}


@app.get("/history")
def trade_history(days: int = 30):
    try:
        from bridge.mt5_connector import get_history_deals
        return get_history_deals(days)
    except Exception as e:
        return {"error": str(e)}


@app.get("/journal")
def journal(limit: int = 50):
    try:
        from db.journal import get_recent_trades
        return get_recent_trades(limit)
    except Exception as e:
        return {"error": str(e)}


@app.get("/stats")
def performance_stats(days: int = 30):
    try:
        from db.journal import get_performance_stats
        return get_performance_stats(days)
    except Exception as e:
        return {"error": str(e)}


@app.get("/news")
def upcoming_news(symbol: str = None, hours: int = 6):
    global _news_cache
    if time.time() - _news_cache["ts"] < 900 and _news_cache["data"]:
        return _news_cache["data"]
    try:
        from bridge.news_filter import NewsFilter
        import yaml
        BASE_DIR = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(BASE_DIR, "config.yaml")) as f:
            cfg = yaml.safe_load(f)
        nf = NewsFilter(cfg)
        result = nf.get_upcoming(symbol=symbol, hours=hours)
        _news_cache = {"data": result, "ts": time.time()}
        return result
    except Exception as e:
        logger.warning(f"News fallback: {e}")
        fallback = [{"title": "News unavailable", "impact": "low"}]
        _news_cache = {"data": fallback, "ts": time.time()}
        return fallback


@app.get("/signal/{symbol}")
def latest_signal(symbol: str):
    try:
        from bridge.bot import get_cached_signal
        sig = get_cached_signal(symbol)
        return sig if sig else {"error": f"No signal for {symbol}"}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bridge.api_server:app", host="0.0.0.0", port=8000, reload=True)