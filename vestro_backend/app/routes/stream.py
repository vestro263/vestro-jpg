"""
WebSocket /api/ws  (and /api/ws/stream for backwards compat)
Pushes score updates to all connected dashboard clients in real-time.
"""
import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, desc
from app.db import AsyncSessionLocal
from app.models import Score, Firm
log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")          # ← prefix added so paths become /api/ws

_connections: set[WebSocket] = set()


async def broadcast(data: dict):
    """Called by scorer.py — sends score update to all live dashboard tabs."""
    if not _connections:
        return
    msg  = json.dumps(data)
    dead = set()
    for ws in list(_connections):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    _connections.difference_update(dead)


async def _handle_ws(websocket: WebSocket):
    """Shared handler for both /api/ws and /api/ws/stream."""
    await websocket.accept()
    _connections.add(websocket)
    log.info(f"WS connected — {len(_connections)} active")

    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(Score, Firm)
                .join(Firm, Firm.id == Score.firm_id)
                .order_by(desc(Score.conviction))
                .limit(20)
            )).all()

        await websocket.send_text(json.dumps([
            {
                "type":       "snapshot",
                "firm_id":    firm.id,
                "firm_name":  firm.name,
                "rise_prob":  score.rise_prob,
                "fall_prob":  score.fall_prob,
                "conviction": score.conviction,
                "top_driver": score.top_driver,
            }
            for score, firm in rows
        ]))

        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if msg == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.debug(f"WS error: {e}")
    finally:
        _connections.discard(websocket)
        log.info(f"WS disconnected — {len(_connections)} active")


@router.websocket("/ws")
async def stream_ws(websocket: WebSocket):
    """Primary path — matches what the frontend connects to: /api/ws"""
    await _handle_ws(websocket)


@router.websocket("/ws/stream")
async def stream_ws_compat(websocket: WebSocket):
    """Legacy path kept for backwards compatibility."""
    await _handle_ws(websocket)