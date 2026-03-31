from fastapi import APIRouter, Query, HTTPException, WebSocket, WebSocketDisconnect
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
import yfinance as yf

router = APIRouter(prefix="/api")

AV_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")

# ─────────────────────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"status": "ok", "service": "vestro-backend"}


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

# ─────────────────────────────────────────────────────────────
# WATCHLIST
# ─────────────────────────────────────────────────────────────

_WATCHLIST = [
    # ── Tech & Software
    "NVDA", "META", "PLTR", "NET", "CRWD",
    "DDOG", "ZS", "SNOW", "MSFT", "GOOGL",
    # ── Fintech & Crypto-adjacent
    "SQ", "SOFI", "HOOD", "COIN", "MSTR",
    "AFRM", "UPST", "NU", "PYPL", "V",
    # ── Consumer & Social
    "RBLX", "SNAP", "PINS", "SPOT", "UBER",
    "ABNB", "LYFT", "DUOL", "RDDT", "DASH",
    # ── Semiconductors
    "ARM", "AMD", "INTC", "QCOM", "AVGO",
    "MRVL", "MU", "SMCI", "TSM", "ASML",
    # ── Biotech & Health
    "MRNA", "BNTX", "RXRX", "TDOC", "HIMS",
    "NVAX", "CRSP", "BEAM", "IONS", "EXAS",
    # ── EV & Clean Energy
    "TSLA", "RIVN", "LCID", "NIO", "XPEV",
    "PLUG", "FCEL", "BE", "ENPH", "SEDG",
    # ── Defence & Space
    "RKLB", "ASTS", "LUNR", "PL", "SPIR",
    "LMT", "NOC", "RTX", "BA", "HII",
]

# ─────────────────────────────────────────────────────────────
# CACHES
# ─────────────────────────────────────────────────────────────

_price_cache: dict = {}
_price_cache_time: datetime | None = None
_PRICE_TTL = timedelta(hours=1)

_overview_cache: dict = {}
_overview_cache_time: datetime | None = None
_OVERVIEW_TTL = timedelta(hours=24)
_overview_loading = False

_firms_cache: list = []
_firms_cache_time: datetime | None = None
_FIRMS_CACHE_TTL = timedelta(hours=1)
_firms_loading = False


# ─────────────────────────────────────────────────────────────
# PRICE FETCH — yfinance parallel
# ─────────────────────────────────────────────────────────────

async def _fetch_prices_yfinance() -> None:
    global _price_cache, _price_cache_time, _yf_info_cache
    loop = asyncio.get_event_loop()

    def _fetch_one(symbol):
        try:
            t    = yf.Ticker(symbol)
            hist = t.history(period="35d")
            info = t.info
            if hist.empty:
                return symbol, None, {}
            closes  = hist["Close"].tolist()[::-1]
            volumes = hist["Volume"].tolist()[::-1]
            return symbol, {
                "closes":            closes,
                "volumes":           volumes,
                "latest_price":      round(float(closes[0]), 2),
                "latest_change_pct": round(
                    (closes[0] - closes[1]) / closes[1] * 100, 2
                ) if len(closes) >= 2 else 0.0,
            }, info
        except Exception:
            return symbol, None, {}

    tasks = [loop.run_in_executor(None, _fetch_one, s) for s in _WATCHLIST]
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=25)
    except asyncio.TimeoutError:
        done, _ = await asyncio.wait(
            [asyncio.ensure_future(t) for t in tasks], timeout=0
        )
        results = [t.result() for t in done if not t.exception()]

    for item in results:
        if item is None:
            continue
        symbol, data, info = item
        if data:
            _price_cache[symbol]   = data
            _yf_info_cache[symbol] = info

    _price_cache_time = datetime.now(timezone.utc)

# ─────────────────────────────────────────────────────────────
# OVERVIEW FETCH — Alpha Vantage (background, optional)
# ─────────────────────────────────────────────────────────────

async def _fetch_overviews_av(client: httpx.AsyncClient) -> None:
    global _overview_cache, _overview_cache_time

    needed = [s for s in _WATCHLIST if s not in _overview_cache][:20]
    if not needed:
        _overview_cache_time = datetime.now(timezone.utc)
        return

    for symbol in needed:
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
        await asyncio.sleep(0.5)

    _overview_cache_time = datetime.now(timezone.utc)


async def _run_overview_fetch():
    global _overview_loading
    _overview_loading = True
    try:
        async with httpx.AsyncClient() as client:
            await _fetch_overviews_av(client)
    finally:
        _overview_loading = False


# ─────────────────────────────────────────────────────────────
# SCORING
# closes[0] = today's partial bar — scoring uses closes[1:] only
# ─────────────────────────────────────────────────────────────

def _score(closes: list, volumes: list) -> dict:
    c = closes[1:]
    v = volumes[1:]

    if len(c) < 5:
        return {"rise_prob": 0.5, "fall_prob": 0.5, "conviction": 50, "top_driver": "insufficient_data"}

    ret_5d  = (c[0] - c[4])  / c[4]
    ret_20d = (c[0] - c[19]) / c[19] if len(c) >= 20 else (c[0] - c[-1]) / c[-1]

    vol_spike = v[0] / (sum(v[1:11]) / 10) if len(v) >= 10 else 1.0

    diffs  = [c[i - 1] - c[i] for i in range(1, min(15, len(c)))]
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

# Add this cache at the top with other caches
_yf_info_cache: dict = {}

def _build_firm(symbol: str) -> dict | None:
    price_data = _price_cache.get(symbol)
    if not price_data:
        return None

    # Use AV overview if available, else yfinance info cache
    info  = _overview_cache.get(symbol, {})
    yinfo = _yf_info_cache.get(symbol, {})
    score = _score(price_data["closes"], price_data["volumes"])

    return {
        "id":                hashlib.md5(symbol.encode()).hexdigest()[:12],
        "name":              info.get("Name") or yinfo.get("longName") or yinfo.get("shortName") or symbol,
        "domain":            (info.get("OfficialSite") or yinfo.get("website") or "").replace("https://", "").replace("http://", "").split("/")[0],
        "sector":            info.get("Sector") or yinfo.get("sector") or yinfo.get("industryDisp") or "Unknown",
        "country":           info.get("Country") or yinfo.get("country") or "US",
        "stage":             "Public",
        "employee_count":    int(info["FullTimeEmployees"]) if info.get("FullTimeEmployees") not in (None, "None", "") else yinfo.get("fullTimeEmployees"),
        "total_funding_usd": int(info["MarketCapitalization"]) if info.get("MarketCapitalization") not in (None, "None", "") else int(yinfo.get("marketCap") or 0),
        "ticker":            symbol,
        "price":             price_data["latest_price"],
        "change_pct":        price_data["latest_change_pct"],
        "score":             score,
    }


# ─────────────────────────────────────────────────────────────
# REFRESH ORCHESTRATOR
# ─────────────────────────────────────────────────────────────

async def _refresh_firms() -> None:
    global _firms_cache, _firms_cache_time, _overview_loading

    now = datetime.now(timezone.utc)

    if _price_cache_time is None or (now - _price_cache_time) > _PRICE_TTL:
        await _fetch_prices_yfinance()

    if AV_KEY and not _overview_loading and (
        _overview_cache_time is None or (now - _overview_cache_time) > _OVERVIEW_TTL
    ):
        asyncio.create_task(_run_overview_fetch())

    _firms_cache = [f for f in (_build_firm(s) for s in _WATCHLIST) if f]
    _firms_cache_time = datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────
# FIRMS ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.get("/firms/debug")
async def firms_debug():
    try:
        await _fetch_prices_yfinance()
        result = _build_firm("NVDA")
        return {
            "status":          "ok",
            "nvda":            result,
            "price_cached":    list(_price_cache.keys()),
            "overview_cached": list(_overview_cache.keys()),
            "av_key_set":      bool(AV_KEY),
        }
    except Exception as e:
        import traceback
        return {"status": "error", "error": str(e), "trace": traceback.format_exc()}


@router.get("/firms/debug2")
async def firms_debug2():
    import traceback
    loop = asyncio.get_event_loop()

    def _fetch_one_verbose(symbol):
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period="5d")
            return {"symbol": symbol, "rows": len(hist), "error": None}
        except Exception as e:
            return {"symbol": symbol, "rows": 0, "error": str(e), "trace": traceback.format_exc()}

    result = await loop.run_in_executor(None, _fetch_one_verbose, "NVDA")
    return result

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


@router.get("/firms/{firm_id}")
async def get_firm(firm_id: str):
    global _firms_loading
    if not _firms_cache:
        if not _firms_loading:
            _firms_loading = True
            try:
                await _refresh_firms()
            finally:
                _firms_loading = False

    for firm in _firms_cache:
        if firm["id"] == firm_id:
            return firm

    raise HTTPException(status_code=404, detail=f"Firm '{firm_id}' not found")


# ─────────────────────────────────────────────────────────────
# SIGNALS
# ─────────────────────────────────────────────────────────────

@router.get("/signals")
async def list_signals(
    firm_id: str | None = None,
    signal_type: str | None = None,
    limit: int = Query(50, le=200),
):
    if not _firms_cache:
        return []

    firms = list(_firms_cache)
    if firm_id:
        firms = [f for f in firms if f["id"] == firm_id]

    signals = []
    for f in firms:
        score      = f.get("score") or {}
        conviction = score.get("conviction", 50)
        rise_prob  = score.get("rise_prob", 0.5)
        top_driver = score.get("top_driver", "unknown")

        if conviction < 55:
            continue

        sig_type = (
            "strong_buy"  if conviction >= 75 and rise_prob >= 0.65 else
            "buy"         if conviction >= 60 and rise_prob >= 0.55 else
            "strong_sell" if conviction >= 75 and rise_prob <= 0.35 else
            "sell"        if conviction >= 60 and rise_prob <= 0.45 else
            "watch"
        )

        if signal_type and sig_type != signal_type:
            continue

        signals.append({
            "id":         hashlib.md5(f"{f['id']}-{sig_type}".encode()).hexdigest()[:12],
            "firm_id":    f["id"],
            "ticker":     f["ticker"],
            "name":       f["name"],
            "type":       sig_type,
            "conviction": conviction,
            "rise_prob":  rise_prob,
            "fall_prob":  score.get("fall_prob", round(1 - rise_prob, 2)),
            "top_driver": top_driver,
            "price":      f.get("price"),
            "change_pct": f.get("change_pct"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    signals.sort(key=lambda s: s["conviction"], reverse=True)
    return signals[:limit]


# ─────────────────────────────────────────────────────────────
# WEBSOCKET — /ws/stream  (consumed by useValuationEngine.js)
# ─────────────────────────────────────────────────────────────

class StreamManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data):
        dead = []
        msg  = json.dumps(data)
        for ws in self.active:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


stream_manager = StreamManager()


@router.websocket("/ws/stream")
async def ws_stream(ws: WebSocket):
    await stream_manager.connect(ws)

    if not _firms_cache:
        try:
            await _refresh_firms()
        except Exception:
            pass

    snapshot = [
        {
            "firm_id":    f["id"],
            "rise_prob":  (f.get("score") or {}).get("rise_prob",  0.5),
            "fall_prob":  (f.get("score") or {}).get("fall_prob",  0.5),
            "conviction": (f.get("score") or {}).get("conviction", 50),
            "top_driver": (f.get("score") or {}).get("top_driver", "unknown"),
        }
        for f in _firms_cache
    ]
    try:
        await ws.send_text(json.dumps(snapshot))
    except Exception:
        stream_manager.disconnect(ws)
        return

    prev_convictions: dict[str, int] = {
        f["id"]: (f.get("score") or {}).get("conviction", 50)
        for f in _firms_cache
    }

    async def _refresh_loop():
        while ws in stream_manager.active:
            await asyncio.sleep(60)
            try:
                await _refresh_firms()
            except Exception:
                continue
            for f in _firms_cache:
                score      = f.get("score") or {}
                conviction = score.get("conviction", 50)
                fid        = f["id"]
                if abs(conviction - prev_convictions.get(fid, conviction)) >= 2:
                    prev_convictions[fid] = conviction
                    try:
                        await ws.send_text(json.dumps({
                            "type":       "score_update",
                            "firm_id":    fid,
                            "rise_prob":  score.get("rise_prob",  0.5),
                            "fall_prob":  score.get("fall_prob",  0.5),
                            "conviction": conviction,
                            "top_driver": score.get("top_driver", "unknown"),
                        }))
                    except Exception:
                        return

    loop_task = asyncio.create_task(_refresh_loop())

    try:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text(json.dumps("pong"))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        loop_task.cancel()
        stream_manager.disconnect(ws)


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


manager          = ConnectionManager()
_account_cache   = {}
_positions_cache: list = []
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
