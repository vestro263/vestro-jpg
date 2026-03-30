from fastapi import APIRouter, Depends, Query, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.db import get_db
from app.models import Firm, Signal, Score
from pydantic import BaseModel
from typing import Optional
import json
from typing import List
from datetime import datetime, timezone, timedelta
import asyncio

router = APIRouter(prefix="/api")

_news_cache: list = []
_news_cache_time: datetime | None = None
_CACHE_TTL = timedelta(minutes=5)

@router.get("/news")
async def get_news(hours: int = 6, symbol: str | None = None):
    global _news_cache, _news_cache_time

    now = datetime.now(timezone.utc)

    # Refresh cache if stale or empty
    if _news_cache_time is None or (now - _news_cache_time) > _CACHE_TTL:
        feeds = [
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
        ]
        events = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0"
        }) as client:
            for feed_url in feeds:
                try:
                    r = await client.get(feed_url)
                    if r.status_code == 200:
                        events.extend(r.json())
                except Exception:
                    continue

        _news_cache = events
        _news_cache_time = now

    if not _news_cache:
        return []

    from dateutil import parser as dateparser
    filtered = []

    for ev in _news_cache:
        if ev.get("impact") not in ("High", "Medium"):
            continue
        try:
            t = dateparser.parse(ev["date"])
            if t.tzinfo is None:
                continue
        except Exception:
            continue

        diff_hours = (t - now).total_seconds() / 3600
        if not (-hours <= diff_hours <= hours):
            continue

        if symbol and ev.get("currency", "") not in symbol.upper():
            continue

        filtered.append({
            "title":    ev.get("title", ""),
            "currency": ev.get("currency", ""),
            "time":     t.isoformat(),
            "tier":     1 if ev.get("impact") == "High" else 2,
        })

    return filtered

@router.get("/health")
async def health():
    return {"status": "ok", "service": "vestro-backend"}

# ─────────────────────────────────────────────────────────────
# FIRMS
# ─────────────────────────────────────────────────────────────

@router.get("/firms")
async def list_firms(
    limit: int = Query(50, le=200),
    sector: str | None = None,
    min_conviction: int = Query(0, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(Firm, Score)
        .outerjoin(Score, Score.firm_id == Firm.id)
        .order_by(desc(Score.conviction))
        .limit(limit)
    )
    if sector:
        q = q.where(Firm.sector == sector)
    if min_conviction:
        q = q.where(Score.conviction >= min_conviction)

    rows = (await db.execute(q)).all()
    return [_firm_row(firm, score) for firm, score in rows]


@router.get("/firms/{firm_id}")
async def get_firm(firm_id: str, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(Firm, Score)
        .outerjoin(Score, Score.firm_id == Firm.id)
        .where(Firm.id == firm_id)
    )).first()

    if not row:
        raise HTTPException(status_code=404, detail="Firm not found")

    firm, score = row
    shap = json.loads(score.shap_json) if score and score.shap_json else {}

    return {
        **_firm_row(firm, score),
        "crunchbase_url": firm.crunchbase_url,
        "last_funding_date": firm.last_funding_date.isoformat() if firm.last_funding_date else None,
        "shap": shap,
    }


def _firm_row(firm: Firm, score: Score | None) -> dict:
    return {
        "id": firm.id,
        "name": firm.name,
        "domain": firm.domain,
        "sector": firm.sector,
        "country": firm.country,
        "stage": firm.stage,
        "employee_count": firm.employee_count,
        "total_funding_usd": firm.total_funding_usd,
        "score": {
            "rise_prob": score.rise_prob,
            "fall_prob": score.fall_prob,
            "conviction": score.conviction,
            "top_driver": score.top_driver,
            "scored_at": score.scored_at.isoformat() if score.scored_at else None,
        } if score else None,
    }

# ─────────────────────────────────────────────────────────────
# SIGNALS
# ─────────────────────────────────────────────────────────────

@router.get("/signals")
async def list_signals(
    firm_id: str | None = None,
    signal_type: str | None = None,
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = select(Signal).order_by(desc(Signal.captured_at)).limit(limit)

    if firm_id:
        q = q.where(Signal.firm_id == firm_id)
    if signal_type:
        q = q.where(Signal.type == signal_type)

    signals = (await db.execute(q)).scalars().all()

    return [
        {
            "id": s.id,
            "firm_id": s.firm_id,
            "type": s.type,
            "value": s.value,
            "text": s.text,
            "source": s.source,
            "captured_at": s.captured_at.isoformat(),
        }
        for s in signals
    ]

# ─────────────────────────────────────────────────────────────
# 🔥 MT5 REAL-TIME SYSTEM
# ─────────────────────────────────────────────────────────────

# WebSocket manager
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(json.dumps(data))
            except:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

manager = ConnectionManager()

# ── CACHE (comes from MT5 push)
_account_cache = {}
_positions_cache = []
_bot_running = False

# ─────────────────────────────────────────────────────────────
# WEBSOCKET
# ─────────────────────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)

    # Send current state immediately
    await ws.send_text(json.dumps({
        "type": "init",
        "account": _account_cache,
        "positions": _positions_cache,
        "bot_running": _bot_running
    }))

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)

# ─────────────────────────────────────────────────────────────
# MT5 PUSH ENDPOINTS (CRITICAL)
# ─────────────────────────────────────────────────────────────

@router.post("/account/update")
async def update_account(data: dict):
    global _account_cache
    _account_cache = data

    await manager.broadcast({
        "type": "account",
        "data": data
    })

    return {"status": "ok"}


@router.post("/positions/update")
async def update_positions(data: list):
    global _positions_cache
    _positions_cache = data

    await manager.broadcast({
        "type": "positions",
        "data": data
    })

    return {"status": "ok"}

# ─────────────────────────────────────────────────────────────
# MT5 READ ENDPOINTS (UI)
# ─────────────────────────────────────────────────────────────

@router.get("/account")
def get_account():
    return _account_cache


@router.get("/positions")
def get_positions():
    return _positions_cache

# ─────────────────────────────────────────────────────────────
# BOT CONTROL
# ─────────────────────────────────────────────────────────────

@router.post("/bot/start")
def start_bot():
    global _bot_running
    _bot_running = True
    return {"status": "started"}


@router.post("/bot/stop")
def stop_bot():
    global _bot_running
    _bot_running = False
    return {"status": "stopped"}


@router.get("/bot/status")
def bot_status():
    return {"running": _bot_running}


class ConnectRequest(BaseModel):
    broker: str
    login: str
    password: str
    server: Optional[str] = None
    meta_account_id: Optional[str] = None

@router.post("/connect")
async def connect(payload: ConnectRequest):
    if payload.broker == "deriv":
        # TODO: validate Deriv PAT token against Deriv API
        # For now, return a mock account so the UI can proceed
        return {
            "status": "connected",
            "account": {
                "account_id": payload.login,
                "broker": "deriv",
                "currency": "USD",
                "balance": 0.0,
            }
        }

    elif payload.broker == "welltrade":
        # TODO: validate via MetaApi using payload.meta_account_id + payload.password
        return {
            "status": "connected",
            "account": {
                "account_id": payload.meta_account_id or payload.login,
                "broker": "welltrade",
                "server": payload.server,
                "currency": "USD",
                "balance": 0.0,
            }
        }

    raise HTTPException(status_code=400, detail=f"Unknown broker: {payload.broker}")