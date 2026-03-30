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
import httpx

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
@router.get("/news/debug")
async def news_debug():
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0"
        }) as client:
            r = await client.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
            events = r.json()

        from dateutil import parser as dateparser
        now = datetime.now(timezone.utc)

        sample = []
        for ev in events:
            if ev.get("impact") not in ("High", "Medium"):
                continue
            try:
                t = dateparser.parse(ev["date"])
                diff = (t - now).total_seconds() / 3600
            except Exception as e:
                diff = f"PARSE ERROR: {e}"
            sample.append({
                "title":      ev["title"],
                "impact":     ev["impact"],
                "date_raw":   ev["date"],
                "diff_hours": round(diff, 2) if isinstance(diff, float) else diff,
            })

        return {"now_utc": now.isoformat(), "count": len(sample), "events": sample}

    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()}


@router.get("/health")
async def health():
    return {"status": "ok", "service": "vestro-backend"}
# ─────────────────────────────────────────────────────────────
# LIVE FIRMS (yfinance)
# ─────────────────────────────────────────────────────────────

@router.get("/firms/debug")
async def firms_debug():
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()

        def _test():
            t = yf.Ticker("NVDA")
            hist = t.history(period="5d")
            info = t.info
            return {
                "hist_rows": len(hist),
                "price": float(hist["Close"].iloc[-1]) if not hist.empty else None,
                "name": info.get("longName"),
                "sector": info.get("sector"),
            }

        result = await asyncio.wait_for(loop.run_in_executor(None, _test), timeout=30)
        return {"status": "ok", "data": result}
    except asyncio.TimeoutError:
        return {"status": "timeout", "error": "yfinance took >30s — Render is blocking outbound requests"}
    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "trace": traceback.format_exc()}

import yfinance as yf
import hashlib

# Curated watchlist — swap/extend as needed
_WATCHLIST = [
    # High-growth tech
    "NVDA", "META", "PLTR", "SNOW", "NET", "DDOG", "CRWD", "MDB", "GTLB", "ZS",
    # Fintech
    "SQ", "AFRM", "SOFI", "HOOD", "NU",
    # Biotech
    "RXRX", "BEAM", "PACB", "TDOC",
    # EV / Energy
    "RIVN", "LCID", "CHPT", "PLUG",
    # Recent IPO / high momentum
    "ARM", "RDDT", "ASTERA",
]

_firms_cache: list = []
_firms_cache_time: datetime | None = None
_FIRMS_CACHE_TTL = timedelta(minutes=10)

def _score_firm(hist, info) -> dict:
    """Score a firm on momentum, volume spike, RSI."""
    closes = hist["Close"].dropna()
    volumes = hist["Volume"].dropna()

    if len(closes) < 10:
        return {"rise_prob": 0.5, "fall_prob": 0.5, "conviction": 0, "top_driver": "insufficient_data"}

    # Momentum: 5d vs 20d return
    ret_5d  = (closes.iloc[-1] - closes.iloc[-5])  / closes.iloc[-5]  if len(closes) >= 5  else 0
    ret_20d = (closes.iloc[-1] - closes.iloc[-20]) / closes.iloc[-20] if len(closes) >= 20 else 0

    # Volume spike: today vs 10d avg
    vol_spike = (volumes.iloc[-1] / volumes.iloc[-10:].mean()) if len(volumes) >= 10 else 1.0

    # RSI (14)
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, 1e-9)
    rsi   = (100 - 100 / (1 + rs)).iloc[-1]

    # Composite score
    momentum_score = (ret_5d * 0.5 + ret_20d * 0.3) * 100
    volume_score   = min((vol_spike - 1) * 20, 30)
    rsi_score      = 20 if 50 < rsi < 70 else (-20 if rsi > 75 or rsi < 30 else 0)

    raw = momentum_score + volume_score + rsi_score
    conviction = max(0, min(100, int(50 + raw)))

    rise_prob = round(min(0.95, max(0.05, 0.5 + raw / 200)), 2)
    fall_prob = round(1 - rise_prob, 2)

    top_driver = (
        "volume_spike"   if vol_spike > 1.5 else
        "momentum_5d"    if abs(ret_5d) > abs(ret_20d) else
        "momentum_20d"   if ret_20d > 0.05 else
        "rsi_signal"
    )

    return {
        "rise_prob":  rise_prob,
        "fall_prob":  fall_prob,
        "conviction": conviction,
        "top_driver": top_driver,
    }

async def _fetch_live_firms() -> list:
    loop = asyncio.get_event_loop()

    def _fetch_one(symbol: str):
        try:
            t    = yf.Ticker(symbol)
            hist = t.history(period="30d")
            info = t.info

            if hist.empty:
                return None

            score = _score_firm(hist, info)
            price = hist["Close"].iloc[-1]
            prev  = hist["Close"].iloc[-2] if len(hist) >= 2 else price
            chg   = ((price - prev) / prev * 100) if prev else 0

            return {
                "id":                hashlib.md5(symbol.encode()).hexdigest()[:12],
                "name":              info.get("longName") or info.get("shortName") or symbol,
                "domain":            info.get("website", "").replace("https://", "").replace("http://", "").split("/")[0],
                "sector":            info.get("sector") or info.get("industryDisp") or "Unknown",
                "country":           info.get("country", "US"),
                "stage":             "Public",
                "employee_count":    info.get("fullTimeEmployees"),
                "total_funding_usd": int(info.get("marketCap", 0)),
                "ticker":            symbol,
                "price":             round(float(price), 2),
                "change_pct":        round(float(chg), 2),
                "score":             score,
            }
        except Exception:
            return None

    # Run all tickers concurrently in thread pool, 20s timeout total
    tasks = [
        loop.run_in_executor(None, _fetch_one, symbol)
        for symbol in _WATCHLIST
    ]
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)
    except asyncio.TimeoutError:
        # Return whatever completed before timeout
        done, _ = await asyncio.wait(
            [asyncio.ensure_future(t) for t in tasks],
            timeout=0
        )
        results = [t.result() for t in done if not t.exception()]

    return [r for r in results if r is not None]

@router.get("/firms")
async def list_firms(
    limit: int = Query(50, le=200),
    sector: str | None = None,
    min_conviction: int = Query(0, ge=0, le=100),
):
    global _firms_cache, _firms_cache_time

    now = datetime.now(timezone.utc)
    if _firms_cache_time is None or (now - _firms_cache_time) > _FIRMS_CACHE_TTL:
        _firms_cache      = await _fetch_live_firms()
        _firms_cache_time = now

    firms = _firms_cache

    if sector:
        firms = [f for f in firms if f.get("sector") == sector]
    if min_conviction:
        firms = [f for f in firms if (f.get("score") or {}).get("conviction", 0) >= min_conviction]

    firms = sorted(firms, key=lambda f: (f.get("score") or {}).get("conviction", 0), reverse=True)
    return firms[:limit]

# ─────────────────────────────────────────────────────────────
# SIGNALS
# ─────────────────────────────────────────────────────────────

@router.get("/signals")
async def list_signals(
    firm_id: str | None = None,
    signal_type: str | None = None,
    limit: int = Query(50, le=200),
):
    # No DB — signals are derived live from yfinance firms cache
    return []
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