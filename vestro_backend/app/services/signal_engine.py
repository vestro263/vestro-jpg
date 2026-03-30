# vestro_backend/app/services/signal_engine.py
import asyncio, httpx, numpy as np
from ..database import SessionLocal
from ..models import Credentials
from ..services.credential_store import decrypt
from . import welltrade, deriv
import os

DERIV_APP_ID = os.environ["DERIV_APP_ID"]

def compute_rsi(closes, period=14):
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_g = np.mean(gains[-period:])
    avg_l = np.mean(losses[-period:])
    if avg_l == 0: return 100
    return 100 - (100 / (1 + avg_g / avg_l))

async def run_signal_loop():
    while True:
        db = SessionLocal()
        try:
            creds = db.query(Credentials).all()
            for cred in creds:
                await process_broker(cred)
        finally:
            db.close()
        await asyncio.sleep(30)

async def process_broker(cred):
    try:
        if cred.broker == "welltrade":
            conn = await welltrade._get_connection(cred.meta_account_id)
            rates = await conn.get_historical_candles("EURUSD", "5m", count=100)
            closes = [r["close"] for r in rates]
            symbol, volume = "EURUSD", 0.01
        else:
            # Deriv: fetch ticks via WS
            api_token = decrypt(cred.password)
            async with __import__("websockets").connect(
                f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
            ) as ws:
                import json
                await ws.send(json.dumps({"authorize": api_token}))
                await ws.recv()
                await ws.send(json.dumps({"ticks_history": "R_100", "count": 100, "end": "latest"}))
                data = json.loads(await ws.recv())
                closes = data["history"]["prices"]
            symbol, volume = "R_100", 1.0

        closes = list(closes)
        rsi = compute_rsi(closes)
        ma_fast = np.mean(closes[-5:])
        ma_slow = np.mean(closes[-20:])

        if rsi < 30 and ma_fast > ma_slow:
            signal = "BUY"
        elif rsi > 70 and ma_fast < ma_slow:
            signal = "SELL"
        else:
            signal = "HOLD"

        print(f"[{cred.broker}] RSI={rsi:.1f} signal={signal}")

        if signal != "HOLD":
            async with httpx.AsyncClient() as client:
                await client.post("http://localhost:8000/api/trade", json={
                    "broker": cred.broker,
                    "symbol": symbol,
                    "action": signal,
                    "volume": volume,
                    "amount": volume,
                })
    except Exception as e:
        print(f"Signal engine error [{cred.broker}]: {e}")