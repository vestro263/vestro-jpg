"""
WebSocket /ws/stream
Pushes score updates to all connected dashboard clients in real-time.
Called by scorer.py after every ML scoring run.
"""
import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, desc
from app.db import AsyncSessionLocal
from app.models import Score, Firm

log = logging.getLogger(__name__)
router = APIRouter()

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


@router.websocket("/ws/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()
    _connections.add(websocket)
    log.info(f"WS connected — {len(_connections)} active")

    try:
        # Send current top-20 scores as snapshot on connect
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

        # Keep-alive ping loop
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if msg == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send our own ping to detect dead connections
                await websocket.send_text(json.dumps({"type": "ping"}))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.debug(f"WS error: {e}")
    finally:
        _connections.discard(websocket)
        log.info(f"WS disconnected — {len(_connections)} active")