import asyncio, httpx, numpy as np
from sqlalchemy import select
from ..database import AsyncSessionLocal
from ..models import Credentials
from ..services.credential_store import decrypt
import os, json, websockets

DERIV_APP_ID  = os.environ["DERIV_APP_ID"]
BACKEND_URL   = os.environ.get("BACKEND_URL", "https://vestro-jpg.onrender.com")

def compute_rsi(closes, period=14):
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_g  = np.mean(gains[-period:])
    avg_l  = np.mean(losses[-period:])
    if avg_l == 0: return 100
    return 100 - (100 / (1 + avg_g / avg_l))

def compute_ma(closes, period):
    return float(np.mean(closes[-period:]))

async def fetch_deriv_ticks(api_token: str, symbol: str = "R_100", count: int = 100):
    url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"authorize": api_token}))
        await ws.recv()
        await ws.send(json.dumps({"ticks_history": symbol, "count": count, "end": "latest"}))
        data = json.loads(await ws.recv())
        return data["history"]["prices"]

async def broadcast_to_frontend(data: dict):
    async with httpx.AsyncClient() as client:
        try:
            await client.post(f"{BACKEND_URL}/api/signal/broadcast", json=data, timeout=5)
        except Exception as e:
            print(f"[signal_engine] broadcast error: {e}")

async def execute_trade(broker: str, symbol: str, action: str, amount: float):
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(f"{BACKEND_URL}/api/trade", json={
                "broker": broker,
                "symbol": symbol,
                "action": action,
                "amount": amount,
            }, timeout=10)
            return res.json()
        except Exception as e:
            print(f"[signal_engine] trade error: {e}")
            return None

async def process_deriv_account(cred):
    try:
        api_token = decrypt(cred.password)
        symbol    = "R_100"
        closes    = list(await fetch_deriv_ticks(api_token, symbol))

        rsi     = compute_rsi(closes)
        ma_fast = compute_ma(closes, 5)
        ma_slow = compute_ma(closes, 20)

        if rsi < 30 and ma_fast > ma_slow:
            signal = "BUY"
        elif rsi > 70 and ma_fast < ma_slow:
            signal = "SELL"
        else:
            signal = "HOLD"

        print(f"[deriv:{cred.user_id}] RSI={rsi:.1f} signal={signal}")

        # Always broadcast signal to frontend
        await broadcast_to_frontend({
            "symbol":  symbol,
            "action":  signal,
            "rsi":     round(rsi, 2),
            "ma_fast": round(ma_fast, 4),
            "ma_slow": round(ma_slow, 4),
            "signal":  {
                "direction": 1 if signal == "BUY" else (-1 if signal == "SELL" else 0),
                "rsi":       round(rsi, 2),
            }
        })

        # Only execute if bot is running and signal is actionable
        async with httpx.AsyncClient() as client:
            status = await client.get(f"{BACKEND_URL}/api/bot/status", timeout=5)
            bot_running = status.json().get("running", False)

        if signal != "HOLD" and bot_running:
            result = await execute_trade("deriv", symbol, signal, 1.0)
            print(f"[deriv:{cred.user_id}] trade result: {result}")

    except Exception as e:
        print(f"[signal_engine] deriv error [{cred.user_id}]: {e}")

async def run_signal_loop():
    print("[signal_engine] starting...")
    while True:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Credentials))
            creds  = result.scalars().all()

        for cred in creds:
            if cred.broker == "deriv":
                await process_deriv_account(cred)

        await asyncio.sleep(30)