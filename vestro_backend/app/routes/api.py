from fastapi import APIRouter, Query, HTTPException, WebSocket, WebSocketDisconnect, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
import json
import asyncio
import httpx
import hashlib
import statistics
import pickle
import pathlib
import os
import yfinance as yf
yf.set_tz_cache_location("/tmp/yfinance_tz_cache")
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Credentials
from ..services.credential_store import decrypt
import websockets

router = APIRouter(prefix="/api")

AV_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")

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

_price_cache:        dict          = {}
_price_cache_time:   datetime|None = None
_overview_cache:     dict          = {}
_overview_cache_time:datetime|None = None
_firms_cache:        list          = []
_firms_cache_time:   datetime|None = None
_firms_loading                     = False
_overview_loading                  = False

_PRICE_TTL       = timedelta(hours=24)
_OVERVIEW_TTL    = timedelta(hours=24)
_FIRMS_CACHE_TTL = timedelta(hours=24)

_CACHE_FILE = pathlib.Path("/tmp/vestro_price_cache.pkl")

# ─────────────────────────────────────────────────────────────
# PRICES — yfinance (all 80 symbols, one request, no rate limit)
# ─────────────────────────────────────────────────────────────

async def _fetch_prices() -> None:
    global _price_cache, _price_cache_time

    # Serve from disk if fresh and non-empty
    if _CACHE_FILE.exists():
        try:
            saved = pickle.loads(_CACHE_FILE.read_bytes())
            age   = datetime.now(timezone.utc) - saved["time"]
            if age < _PRICE_TTL and len(saved["data"]) > 0:
                _price_cache      = saved["data"]
                _price_cache_time = saved["time"]
                return
        except Exception:
            pass

    def _fetch_all():
        tickers = yf.download(
            " ".join(_WATCHLIST),
            period="40d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        result = {}
        for symbol in _WATCHLIST:
            try:
                df = tickers[symbol].dropna()
                if len(df) < 5:
                    continue
                closes  = [float(x) for x in df["Close"].tolist()]
                volumes = [float(x) for x in df["Volume"].tolist()]
                result[symbol] = {
                    "closes":            closes,
                    "volumes":           volumes,
                    "latest_price":      round(closes[-1], 2),
                    "latest_change_pct": round((closes[-1] - closes[-2]) / closes[-2] * 100, 2),
                }
            except Exception:
                continue
        return result

    loop = asyncio.get_event_loop()
    _price_cache      = await loop.run_in_executor(None, _fetch_all)
    _price_cache_time = datetime.now(timezone.utc)

    try:
        _CACHE_FILE.write_bytes(pickle.dumps({
            "data": _price_cache,
            "time": _price_cache_time,
        }))
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────
# OVERVIEWS — Alpha Vantage (metadata: name, sector, market cap)
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
        await asyncio.sleep(62)  # AV free tier: 5 req/min

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
# closes[-1] = most recent bar; uses full history
# ─────────────────────────────────────────────────────────────

def _score(closes: list, volumes: list) -> dict:
    c = closes
    v = volumes

    if len(c) < 5:
        return {"rise_prob": 0.5, "fall_prob": 0.5, "conviction": 50, "top_driver": "insufficient_data"}

    ret_5d  = (c[-1] - c[-5])  / c[-5]
    ret_20d = (c[-1] - c[-20]) / c[-20] if len(c) >= 20 else (c[-1] - c[0]) / c[0]

    vol_spike = v[-1] / (sum(v[-11:-1]) / 10) if len(v) >= 11 else 1.0

    diffs  = [c[i] - c[i - 1] for i in range(max(1, len(c) - 14), len(c))]
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

    info  = _overview_cache.get(symbol, {})
    score = _score(price_data["closes"], price_data["volumes"])

    return {
        "id":                hashlib.md5(symbol.encode()).hexdigest()[:12],
        "name":              info.get("Name") or symbol,
        "domain":            (info.get("OfficialSite") or "").replace("https://", "").replace("http://", "").split("/")[0],
        "sector":            info.get("Sector") or "Unknown",
        "country":           info.get("Country") or "US",
        "stage":             "Public",
        "employee_count":    int(info["FullTimeEmployees"]) if info.get("FullTimeEmployees") not in (None, "None", "") else None,
        "total_funding_usd": int(info["MarketCapitalization"]) if info.get("MarketCapitalization") not in (None, "None", "") else 0,
        "ticker":            symbol,
        "price":             price_data["latest_price"],
        "change_pct":        price_data["latest_change_pct"],
        "score":             score,
    }

# ─────────────────────────────────────────────────────────────
# REFRESH ORCHESTRATOR
# ─────────────────────────────────────────────────────────────

async def _refresh_firms() -> None:
    global _firms_cache, _firms_cache_time

    now = datetime.now(timezone.utc)

    if _price_cache_time is None or (now - _price_cache_time) > _PRICE_TTL:
        await _fetch_prices()

    if AV_KEY and not _overview_loading and (
        _overview_cache_time is None or (now - _overview_cache_time) > _OVERVIEW_TTL
    ):
        asyncio.create_task(_run_overview_fetch())

    _firms_cache      = [f for f in (_build_firm(s) for s in _WATCHLIST) if f]
    _firms_cache_time = datetime.now(timezone.utc)

# ─────────────────────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"status": "ok", "service": "vestro-backend"}

# ─────────────────────────────────────────────────────────────
# NEWS
# ─────────────────────────────────────────────────────────────

_news_cache:      list          = []
_news_cache_time: datetime|None = None
_NEWS_TTL = timedelta(minutes=5)


@router.get("/news")
async def get_news(hours: int = 6, symbol: str | None = None):
    global _news_cache, _news_cache_time

    now = datetime.now(timezone.utc)

    if _news_cache_time is None or (now - _news_cache_time) > _NEWS_TTL:
        feeds = [
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
        ]
        events = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
            for feed_url in feeds:
                try:
                    r = await client.get(feed_url)
                    if r.status_code == 200:
                        events.extend(r.json())
                except Exception:
                    continue
        _news_cache      = events
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

        if not (-hours <= (t - now).total_seconds() / 3600 <= hours):
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
# FIRMS ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.get("/firms/debug")
async def firms_debug():
    try:
        await _fetch_prices()
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


@router.post("/firms/refresh")
async def force_refresh():
    global _firms_cache_time, _price_cache_time
    _firms_cache_time = None
    _price_cache_time = None
    if _CACHE_FILE.exists():
        _CACHE_FILE.unlink()
    await _refresh_firms()
    return {"firms_loaded": len(_firms_cache), "prices_loaded": len(_price_cache)}


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

    firms.sort(key=lambda f: (f.get("score") or {}).get("conviction", 0), reverse=True)
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


@router.post("/signal/broadcast")
async def broadcast_signal(data: dict):
    await manager.broadcast({"type": "signal", **data})
    return {"status": "ok"}


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


@router.post("/contract/update")
async def contract_update(data: dict):
    await manager.broadcast({"type": "contract_update", **data})
    return {"status": "ok"}


@router.get("/account")
def get_account():
    return _account_cache


@router.get("/positions")
def get_positions():
    return _positions_cache


@router.get("/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Credentials))
    creds  = result.scalars().all()

    accounts = []
    for cred in creds:
        try:
            api_token = decrypt(cred.password)
            async with websockets.connect(
                f"wss://ws.binaryws.com/websockets/v3?app_id={os.getenv('DERIV_APP_ID', '1089')}"
            ) as ws:
                await ws.send(json.dumps({"authorize": api_token}))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))

            if "error" in resp:
                continue

            auth = resp["authorize"]
            accounts.append({
                "account_id": auth.get("loginid"),
                "balance":    auth.get("balance", 0.0),
                "currency":   auth.get("currency", "USD"),
                "name":       auth.get("fullname", ""),
                "type":       "demo" if auth.get("is_virtual", 0) == 1 else "real",
                "broker":     "deriv",
            })
        except Exception as e:
            print(f"[accounts] failed for {cred.user_id}: {e}")
            continue

    return accounts

# ─────────────────────────────────────────────────────────────
# BOT CONTROL
# ─────────────────────────────────────────────────────────────

_BOT_STATE_FILE = "/tmp/vestro_bot_running.txt"


def _read_bot_state() -> bool:
    try:
        return open(_BOT_STATE_FILE).read().strip() == "1"
    except Exception:
        return False


def _write_bot_state(running: bool):
    try:
        open(_BOT_STATE_FILE, "w").write("1" if running else "0")
    except Exception:
        pass


@router.post("/bot/start")
def start_bot():
    global _bot_running
    _bot_running = True
    _write_bot_state(True)
    return {"status": "started"}


@router.post("/bot/stop")
def stop_bot():
    global _bot_running
    _bot_running = False
    _write_bot_state(False)
    return {"status": "stopped"}


@router.get("/bot/status")
def bot_status():
    global _bot_running
    _bot_running = _read_bot_state()
    return {"running": _bot_running}

# ─────────────────────────────────────────────────────────────
# JOURNAL  —  replace @router.get("/journal") in routes/api.py
# ─────────────────────────────────────────────────────────────

@router.get("/journal")
async def get_journal(
    account_id: str | None = Query(None),
    email:      str | None = Query(None),
    limit:      int = Query(50, le=500),
    symbol:     str | None = Query(None),
    strategy:   str | None = Query(None),
    db:         AsyncSession = Depends(get_db),
):
    from sqlalchemy import text

    # -------------------------------------------------
    # Resolve usable trading account ids
    # -------------------------------------------------
    account_ids = set()

    # Direct account_id passed by frontend
    if account_id:
        account_ids.add(account_id)

    # Resolve via email -> users -> credentials.user_id
    if email:
        rows = await db.execute(text("""
            SELECT c.user_id
            FROM users u
            JOIN credentials c
              ON c.google_user_id = u.id
            WHERE LOWER(u.email) = LOWER(:email)
        """), {"email": email})

        for r in rows.fetchall():
            if r[0]:
                account_ids.add(r[0])

    # If nothing resolved, fall back to legacy rows
    if not account_ids:
        account_ids.add("default_account")

    # -------------------------------------------------
    # Build WHERE clause
    # -------------------------------------------------
    filters = [
        "outcome IS NOT NULL",
        "signal IN ('BUY', 'SELL')",
    ]

    params = {"limit": limit}

    acc_placeholders = []
    for i, acc in enumerate(account_ids):
        key = f"acc_{i}"
        acc_placeholders.append(f":{key}")
        params[key] = acc

    # Include legacy NULL rows only when using fallback
    if account_ids == {"default_account"}:
        filters.append(
            f"(account_id IN ({','.join(acc_placeholders)}) OR account_id IS NULL)"
        )
    else:
        filters.append(f"account_id IN ({','.join(acc_placeholders)})")

    if symbol:
        filters.append("symbol = :symbol")
        params["symbol"] = symbol

    if strategy:
        filters.append("strategy = :strategy")
        params["strategy"] = strategy

    where = " AND ".join(filters)

    # -------------------------------------------------
    # Query journal rows
    # -------------------------------------------------
    rows = await db.execute(text(f"""
        SELECT
            id,
            strategy,
            symbol,
            signal,
            confidence,
            entry_price,
            exit_price,
            outcome,
            executed_at,
            captured_at,
            atr_zone,
            executed,
            account_id
        FROM signal_logs
        WHERE {where}
        ORDER BY captured_at DESC
        LIMIT :limit
    """), params)

    results = []

    for r in rows.fetchall():
        entry_price = r[5]
        exit_price  = r[6]
        signal_dir  = str(r[3]).upper()
        outcome     = r[7]

        profit = None
        if entry_price is not None and exit_price is not None:
            direction = 1 if signal_dir in ("BUY", "CALL", "RISE") else -1
            profit = round((exit_price - entry_price) * direction, 5)

        if profit is None:
            profit = 1.0 if outcome == "WIN" else (-1.0 if outcome == "LOSS" else 0.0)

        results.append({
            "ticket":      str(r[0]),
            "symbol":      r[2],
            "strategy":    r[1],
            "type":        signal_dir,
            "volume":      0.0,
            "open_price":  entry_price,
            "close_price": exit_price,
            "open_time":   str(r[9]) if r[9] else "—",
            "close_time":  str(r[8]) if r[8] else "—",
            "swap":        0.0,
            "commission":  0.0,
            "profit":      profit,
            "outcome":     outcome,
            "confidence":  r[4],
            "atr_zone":    r[10],
            "executed":    r[11],
            "account_id":  r[12],
        })

    return results
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
        raise HTTPException(status_code=501, detail="WelTrade not yet implemented")

    raise HTTPException(status_code=400, detail=f"Unknown broker: {payload.broker}")