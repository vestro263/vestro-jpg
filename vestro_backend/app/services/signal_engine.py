"""
signal_engine.py
================
Delegates all strategy logic to app/services/strategies/.
Original process_deriv_account loop is preserved untouched.
StrategyRunner boots V75 + Crash500 in parallel on startup.
Balance is fetched live from Deriv using the selected account token.
"""

import asyncio
import httpx
import numpy as np
from sqlalchemy import select
from ..database import AsyncSessionLocal
from ..models import Credentials
from ..services.credential_store import decrypt
import os
import json
import websockets

from .strategies.strategy_runner import StrategyRunner

DERIV_APP_ID = os.environ["DERIV_APP_ID"]
BACKEND_URL  = os.environ.get("BACKEND_URL", "https://vestro-jpg.onrender.com")


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def compute_rsi(closes, period=14):
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_g  = np.mean(gains[-period:])
    avg_l  = np.mean(losses[-period:])
    if avg_l == 0:
        return 100
    return 100 - (100 / (1 + avg_g / avg_l))

def compute_ema(closes, period):
    closes = list(closes)
    k   = 2 / (period + 1)
    ema = closes[-1]
    for price in reversed(closes[:-1]):
        ema = price * k + ema * (1 - k)
    return round(ema, 4)

def compute_adx(closes, period=14):
    if len(closes) < period + 1:
        return 0.0
    highs = [max(closes[i], closes[i-1]) for i in range(1, len(closes))]
    lows  = [min(closes[i], closes[i-1]) for i in range(1, len(closes))]
    trs   = [h - l for h, l in zip(highs, lows)]
    return round(float(np.mean(trs[-period:]) / np.mean(closes[-period:]) * 100), 2)

def compute_atr(closes, period=14):
    if len(closes) < period + 1:
        return 0.0
    trs = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
    return round(float(np.mean(trs[-period:])), 5)

def compute_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow:
        return 0.0
    ema_fast = compute_ema(closes[-fast*2:], fast)
    ema_slow = compute_ema(closes[-slow*2:], slow)
    return round(ema_fast - ema_slow, 5)

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


# ============================================================
# LIVE BALANCE FETCH
# Reuses the authorize handshake — Deriv returns the balance
# of the selected account inside the authorize response for free.
# ============================================================

async def fetch_deriv_balance(api_token: str) -> float:
    url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"authorize": api_token}))
            auth     = json.loads(await ws.recv())
            balance  = float(auth["authorize"]["balance"])
            currency = auth["authorize"].get("currency", "USD")
            print(f"[signal_engine] live balance: {balance} {currency}")
            return balance
    except Exception as e:
        print(f"[signal_engine] balance fetch error: {e}")
        return 0.0


# ============================================================
# ORIGINAL DERIV ACCOUNT LOOP (unchanged)
# ============================================================

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

        await broadcast_to_frontend({
            "symbol":  symbol,
            "action":  signal,
            "rsi":     round(rsi, 2),
            "ma_fast": round(ma_fast, 4),
            "ma_slow": round(ma_slow, 4),
            "signal": {
                "direction": 1 if signal == "BUY" else (-1 if signal == "SELL" else 0),
                "rsi":       round(rsi, 2),
            }
        })

        async with httpx.AsyncClient() as client:
            status = await client.get(f"{BACKEND_URL}/api/bot/status", timeout=5)
            bot_running = status.json().get("running", False)

        if signal != "HOLD" and bot_running:
            result = await execute_trade("deriv", symbol, signal, 1.0)
            print(f"[deriv:{cred.user_id}] trade result: {result}")

    except Exception as e:
        print(f"[signal_engine] deriv error [{cred.user_id}]: {e}")


# ============================================================
# MAIN LOOP
# ============================================================

_strategy_runner_task = None   # Ensures runner only starts once


async def run_signal_loop():
    global _strategy_runner_task
    print("[signal_engine] starting...")

    # ── Fetch credentials ─────────────────────────────────────
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Credentials))
        creds  = result.scalars().all()

    deriv_cred = next((c for c in creds if c.broker == "deriv"), None)

    # ── Boot strategy runner ONCE on startup ──────────────────
    if deriv_cred and _strategy_runner_task is None:
        api_token = decrypt(deriv_cred.password)

        # Pull live balance from the selected Deriv account
        balance = await fetch_deriv_balance(api_token)

        runner = StrategyRunner(
            api_token        = api_token,
            balance          = balance,
            broadcast_fn     = broadcast_to_frontend,
            execute_trade_fn = execute_trade,
            is_prop          = False,
        )

        _strategy_runner_task = asyncio.create_task(
            runner.start(),
            name="strategy-runner"
        )
        print("[signal_engine] StrategyRunner booted — V75 + Crash500 running in parallel ✓")

    # ── Original loop continues unchanged ─────────────────────
    while True:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Credentials))
            creds  = result.scalars().all()

        for cred in creds:
            if cred.broker == "deriv":
                await process_deriv_account(cred)

        await asyncio.sleep(30)