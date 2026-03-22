"""
FastAPI Server — REST + WebSocket API for the React dashboard.
Broadcasts live signal events to all connected clients.
"""

import asyncio
import json
import logging
import time
import os
from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

app = FastAPI(title="Forex Bot API", version="1.0.0")

# ✅ Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── WebSocket connection manager ───────────────────────────────────────────
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


# ── Broadcast helpers ──────────────────────────────────────────────────────
async def broadcast_event(event: dict):
    await manager.broadcast(event)


def broadcast_sync(event: dict):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(broadcast_event(event), loop)
        else:
            loop.run_until_complete(broadcast_event(event))
    except Exception as e:
        logger.error(f"Broadcast error: {e}")


# ── WebSocket endpoint ─────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)


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
        return {"error": str(e)}


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
def performance_stats():
    try:
        from db.journal import get_performance_stats
        return get_performance_stats()
    except Exception as e:
        return {"error": str(e)}


# ── NEWS ENDPOINT (SAFE + FALLBACK) ─────────────────────────────────────────
@app.get("/news")
def upcoming_news(symbol: str = None, hours: int = 6):
    """
    Returns upcoming news events.
    Falls back to mock data if real news module fails.
    """
    try:
        from bridge.news_filter import NewsFilter
        import yaml

        BASE_DIR = os.path.dirname(os.path.dirname(__file__))
        CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)

        nf = NewsFilter(cfg)
        return nf.get_upcoming(symbol=symbol, hours=hours)

    except Exception as e:
        logger.warning(f"News fallback triggered: {e}")

        # 🔥 Fallback so frontend never breaks
        return [
            {"title": "USD strong after Fed decision", "impact": "high"},
            {"title": "Gold consolidating", "impact": "medium"},
            {"title": "Oil slightly down", "impact": "low"},
        ]


@app.get("/signal/{symbol}")
def latest_signal(symbol: str):
    try:
        from bridge.bot import get_cached_signal
        sig = get_cached_signal(symbol)
        if sig:
            return sig
        return {"error": f"No signal for {symbol}"}
    except Exception as e:
        return {"error": str(e)}


# ── RUN SERVER ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bridge.api_server:app", host="0.0.0.0", port=8000, reload=True)