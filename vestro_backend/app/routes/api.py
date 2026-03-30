from fastapi import APIRouter, Depends, Query, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.db import get_db
from app.models import Firm, Signal, Score
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
import json
import asyncio
import httpx
import hashlib
import statistics
import os

router = APIRouter(prefix="/api")

AV_KEY = os.getenv("ALPHA_VANTAGE_KEY", "demo")

# ─────────────────────────────────────────────────────────────
# NEWS
# ─────────────────────────────────────────────────────────────

_news_cache: list = []
_news_cache_time: datetime | None = None
_CACHE_TTL = timedelta(minutes=5)


@router.get("/news")
async def get_news(hours: int = 6, symbol: str | None = None):
    global _news_cache, _news_cache_time

    now = datetime.now(timezone.utc)

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


# ─────────────────────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"status": "ok", "service": "vestro-backend"}


# ─────────────────────────────────────────────────────────────
# LIVE FIRMS — split cache (overview 24h, prices 10min)
# Each ticker = 1 price call per 10min + 1 overview call per 24h
# 60 tickers = 60 calls/refresh — well within free tier
# ─────────────────────────────────────────────────────────────

_WATCHLIST = [
    # ── Tech & Software ───────────────────────────────────────
    "NVDA", "META", "PLTR", "NET", "CRWD",
    "DDOG", "ZS", "SNOW", "MSFT", "GOOGL",

    # ── Fintech & Crypto-adjacent ─────────────────────────────
    "SQ", "SOFI", "HOOD", "COIN", "MSTR",
    "AFRM", "UPST", "NU", "PYPL", "V",

    # ── Consumer & Social ─────────────────────────────────────
    "RBLX", "SNAP", "PINS", "SPOT", "UBER",
    "ABNB", "LYFT", "DUOL", "RDDT", "DASH",

    # ── Semiconductors ────────────────────────────────────────
    "ARM", "AMD", "INTC", "QCOM", "AVGO",
    "MRVL", "MU", "SMCI", "TSM", "ASML",

    # ── Biotech & Health ──────────────────────────────────────
    "MRNA", "BNTX", "RXRX", "TDOC", "HIMS",
    "NVAX", "CRSP", "BEAM", "IONS", "EXAS",

    # ── EV & Clean Energy ─────────────────────────────────────
    "TSLA", "RIVN", "LCID", "NIO", "XPEV",
    "PLUG", "FCEL", "BE", "ENPH", "SEDG",

    # ── Defence & Space ───────────────────────────────────────
    "RKLB", "ASTS", "LUNR", "PL", "SPIR",
    "LMT", "NOC", "RTX", "BA", "HII",
]

# Overview cache — name, sector, employees, market cap (24h TTL)
_overview_cache: dict      = {}
_overview_cache_time: datetime | None = None
_OVERVIEW_TTL = timedelta(hours=24)

# Price cache — OHLCV closes + volumes (10min TTL)
_price_cache: dict         = {}
_price_cache_time: datetime | None = None
_PRICE_TTL = timedelta(minutes=10)

_firms_cache: list         = []
_firms_cache_time: datetime | None = None
_FIRMS_CACHE_TTL = timedelta(minutes=10)
_firms_loading             = False


async def _fetch_overviews(client: httpx.AsyncClient) -> None:
    """1 API call per ticker, runs once every 24h."""
    global _overview_cache, _overview_cache_time

    async def _one(symbol: str):
        try:
            r = await client.get(
                "https://www.alphavantage.co/query",
                params={"function": "OVERVIEW", "symbol": symbol, "apikey": AV_KEY},
                timeout=15,
            )
            data = r.json()
            if data.get("Symbol"):
                _overview_cache[symbol] = data
        except Exception:
            pass

    await asyncio.gather(*[_one(s) for s in _WATCHLIST])
    _overview_cache_time = datetime.now(timezone.utc)


async def _fetch_prices(client: httpx.AsyncClient) -> None:
    """1 API call per ticker, runs every 10 min."""
    global _price_cache, _price_cache_time

    async def _one(symbol: str):
        try:
            r = await client.get(
                "https://www.alphavantage.co/query",
                params={
                    "function":   "TIME_SERIES_DAILY",
                    "symbol":     symbol,
                    "outputsize": "compact",
                    "apikey":     AV_KEY,
                },
                timeout=15,
            )
            ts = r.json().get("Time Series (Daily)", {})
            if not ts:
                return
            dates   = sorted(ts.keys(), reverse=True)
            closes  = [float(ts[d]["4. close"]) for d in dates[:30]]
            volumes = [float(ts[d]["5. volume"]) for d in dates[:30]]
            if closes:
                _price_cache[symbol] = {"closes": closes, "volumes": volumes}
        except Exception:
            pass

    await asyncio.gather(*[_one(s) for s in _WATCHLIST])
    _price_cache_time = datetime.now(timezone.utc)


def _score(closes: list, volumes: list) -> dict:
    if len(closes) < 5:
        return {"rise_prob": 0.5, "fall_prob": 0.5, "conviction": 50, "top_driver": "insufficient_data"}

    ret_5d    = (closes[0] - closes[4]) / closes[4]
    ret_20d   = (closes[0] - closes[19]) / closes[19] if len(closes) >= 20 else 0
    vol_spike = volumes[0] / (sum(volumes[1:11]) / 10) if len(volumes) >= 10 else 1.0

    diffs  = [closes[i - 1] - closes[i] for i in range(1, min(15, len(closes)))]
    gains  = [d for d in diffs if d > 0]
    losses = [-d for d in diffs if d < 0]
    avg_g  = statistics.mean(gains)  if gains  else 0.001
    avg_l  = statistics.mean(losses) if losses else 0.001
    rsi    = 100 - 100 / (1 + avg_g / avg_l)

    momentum_score = (ret_5d * 0.5 + ret_20d * 0.3) * 100
    volume_score   = min((vol_spike - 1) * 20, 30)
    rsi_score      = 20 if 50 < rsi < 70 else (-20 if rsi > 75 or rsi < 30 else 0)
    raw            = momentum_score + volume_score + rsi_score
    conviction     = max(0, min(100, int(50 + raw)))
    rise_prob      = round(min(0.95, max(0.05, 0.5 + raw / 200)), 2)

    top_driver = (
        "volume_spike" if vol_spike > 1.5 else
        "momentum_5d"  if abs(ret_5d) > abs(ret_20d) else
        "momentum_20d" if ret_20d > 0.05 else
        "rsi_signal"
    )

    return {
        "rise_prob":  rise_prob,
        "fall_prob":  round(1 - rise_prob, 2),
        "conviction": conviction,
        "top_driver": top_driver,
    }


def _build_firm(symbol: str) -> dict | None:
    price_data = _price_cache.get(symbol)
    if not price_data:
        return None

    closes  = price_data["closes"]
    volumes = price_data["volumes"]
    info    = _overview_cache.get(symbol, {})
    score   = _score(closes, volumes)

    return {
        "id":                hashlib.md5(symbol.encode()).hexdigest()[:12],
        "name":              info.get("Name") or symbol,
        "domain":            info.get("OfficialSite", "").replace("https://", "").replace("http://", "").split("/")[0],
        "sector":            info.get("Sector") or "Unknown",
        "country":           info.get("Country", "US"),
        "stage":             "Public",
        "employee_count":    int(info["FullTimeEmployees"]) if info.get("FullTimeEmployees", "None") not in ("None", "") else None,
        "total_funding_usd": int(info["MarketCapitalization"]) if info.get("MarketCapitalization", "None") not in ("None", "") else 0,
        "ticker":            symbol,
        "price":             round(closes[0], 2),
        "change_pct":        round((closes[0] - closes[1]) / closes[1] * 100, 2) if len(closes) >= 2 else 0,
        "score":             score,
    }


async def _refresh_firms() -> None:
    """Refresh stale caches then rebuild firms list."""
    global _firms_cache, _firms_cache_time

    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient() as client:
        tasks = []
        if _overview_cache_time is None or (now - _overview_cache_time) > _OVERVIEW_TTL:
            tasks.append(_fetch_overviews(client))   # once per 24h
        if _price_cache_time is None or (now - _price_cache_time) > _PRICE_TTL:
            tasks.append(_fetch_prices(client))      # every 10 min
        if tasks:
            await asyncio.gather(*tasks)

    _firms_cache      = [f for f in (_build_firm(s) for s in _WATCHLIST) if f]
    _firms_cache_time = datetime.now(timezone.utc)


@router.get("/firms/debug")
async def firms_debug():
    try:
        async with httpx.AsyncClient() as client:
            await _fetch_prices(client)
        result = _build_firm("NVDA")
        return {
            "status":           "ok",
            "data":             result,
            "price_cache_keys": list(_price_cache.keys()),
            "overview_cached":  list(_overview_cache.keys()),
        }
    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "trace": traceback.format_exc()}


@router.get("/firms")
async def list_firms(
    limit: int = Query(50, le=200),
    sector: str | None = None,
    min_conviction: int = Query(0, ge=0, le=100),
):
    global _firms_loading

    now = datetime.now(timezone.utc)
    if _firms_cache_time is None or (now - _firms_cache_time) > _FIRMS_CACHE_TTL:
        if not _firms_loading:
            _firms_loading = True
            try:
                await _refresh_firms()
            finally:
                _firms_loading = False

    firms = list(_firms_cache)
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
    return []


# ─────────────────────────────────────────────────────────────
# MT5 REAL-TIME SYSTEM
# ─────────────────────────────────────────────────────────────

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
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()

_account_cache   = {}
_positions_cache = []
_bot_running     = False


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    await ws.send_text(json.dumps({
        "type":        "init",
        "account":     _account_cache,
        "positions":   _positions_cache,
        "bot_running": _bot_running,
    }))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


@router.post("/account/update")
async def update_account(data: dict):
    global _account_cache
    _account_cache = data
    await manager.broadcast({"type": "account", "data": data})
    return {"status": "ok"}


@router.post("/positions/update")
async def update_positions(data: list):
    global _positions_cache
    _positions_cache = data
    await manager.broadcast({"type": "positions", "data": data})
    return {"status": "ok"}


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


# ─────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────

class ConnectRequest(BaseModel):
    broker: str
    login: str
    password: str
    server: Optional[str] = None
    meta_account_id: Optional[str] = None


@router.post("/connect")
async def connect(payload: ConnectRequest):
    if payload.broker == "deriv":
        try:
            import websockets
            async with websockets.connect(
                "wss://ws.binaryws.com/websockets/v3?app_id=1089"
            ) as deriv_ws:
                await deriv_ws.send(json.dumps({"authorize": payload.password}))
                response = json.loads(await asyncio.wait_for(deriv_ws.recv(), timeout=10))

            if "error" in response:
                raise HTTPException(
                    status_code=401,
                    detail=response["error"].get("message", "Invalid Deriv API token"),
                )

            auth = response["authorize"]
            return {
                "status": "connected",
                "account": {
                    "account_id": auth.get("loginid"),
                    "broker":     "deriv",
                    "currency":   auth.get("currency", "USD"),
                    "balance":    auth.get("balance", 0.0),
                    "name":       auth.get("fullname", ""),
                    "email":      auth.get("email", ""),
                    "is_virtual": auth.get("is_virtual", 0) == 1,
                },
            }
        except HTTPException:
            raise
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Deriv API timed out")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Deriv connection failed: {str(e)}")

    elif payload.broker == "welltrade":
        raise HTTPException(status_code=501, detail="WelTrade validation not yet implemented")

    raise HTTPException(status_code=400, detail=f"Unknown broker: {payload.broker}")

@router.get("/firms/key-check")
async def key_check():
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": "NVDA",
                "outputsize": "compact",
                "apikey": AV_KEY,
            },
            timeout=15,
        )
        body = r.json()
    return {
        "key_used": AV_KEY[:6] + "...",   # show first 6 chars only
        "response_keys": list(body.keys()),
        "has_data": "Time Series (Daily)" in body,
        "error": body.get("Note") or body.get("Information") or body.get("Error Message"),
    }