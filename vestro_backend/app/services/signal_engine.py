"""
signal_engine.py
================
Delegates all strategy logic to app/services/strategies/.
Original process_deriv_account loop is preserved but GUARDED —
it skips execution when StrategyRunner is active to prevent
two systems trading the same account simultaneously.

FIXES:
  - execute_trade_fn signature now matches V75Strategy.execute() call
  - StrategyRunner auto-restarts if its task dies
  - process_deriv_account skips trade execution when runner is live
  - balance re-fetched on each runner restart
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
# UTILITY FUNCTIONS  (unchanged)
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


async def fetch_deriv_ticks(api_token: str, symbol: str = "R_100", count: int = 300):
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


# FIX 1: signature matches V75Strategy.execute() call:
#   execute_trade_fn(symbol=..., action=..., amount=...)
# broker is always "deriv" here — strategies don't need to pass it.
async def execute_trade(symbol: str, action: str, amount: float, broker: str = "deriv"):
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
# LIVE BALANCE FETCH  (unchanged)
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
# ORIGINAL DERIV ACCOUNT LOOP
# FIX 3: trade execution skipped when StrategyRunner is live.
# Signal broadcast still runs so the frontend stays updated.
# ============================================================

async def process_deriv_account(cred, runner_is_live: bool = False):
    for symbol in ["R_100", "R_75"]:   # ← add R_75 here
        try:
            api_token = decrypt(cred.password)
            closes    = list(await fetch_deriv_ticks(api_token, symbol))

            rsi     = compute_rsi(closes)
            ma_fast = compute_ma(closes, 5)
            ma_slow = compute_ma(closes, 20)
            atr_val  = compute_atr(closes)
            adx_val  = compute_adx(closes)
            macd_val = compute_macd(closes)
            ema50    = compute_ema(closes, 50)
            ema200   = compute_ema(closes, 200)
            ema21    = compute_ema(closes, 21)

            tss = 0
            if ema21 > ema50 > ema200 or ema21 < ema50 < ema200: tss += 1
            if adx_val > 25:                                      tss += 1
            if closes[-1] > ema200:                               tss += 1
            if macd_val > 0:                                      tss += 1
            tss += 1

            if len(closes) >= 21:
                atrs    = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
                avg_atr = float(np.mean(atrs[-21:-1]))
                ratio   = atr_val / (avg_atr + 1e-10)
                if ratio < 0.5:   atr_zone = "low"
                elif ratio < 1.5: atr_zone = "normal"
                elif ratio < 2.5: atr_zone = "elevated"
                else:             atr_zone = "extreme"
            else:
                atr_zone = "normal"

            if rsi < 40 and ma_fast > ma_slow and tss >= 2 and atr_zone != "extreme":
                signal = "BUY"
            elif rsi > 60 and ma_fast < ma_slow and tss >= 2 and atr_zone != "extreme":
                signal = "SELL"
            else:
                signal = "HOLD"

            print(f"[deriv:{cred.user_id}:{symbol}] RSI={rsi:.1f} TSS={tss}/5 "
                  f"ATR_ZONE={atr_zone} signal={signal} "
                  f"{'(runner active — skipping execution)' if runner_is_live else ''}")

            await broadcast_to_frontend({
                "symbol": symbol,
                "action": signal,
                "signal": {
                    "direction":  1 if signal == "BUY" else (-1 if signal == "SELL" else 0),
                    "rsi":        round(rsi, 2),
                    "adx":        round(adx_val, 2),
                    "atr":        round(atr_val, 5),
                    "ema50":      round(ema50, 4),
                    "ema200":     round(ema200, 4),
                    "macd_hist":  round(macd_val, 5),
                    "tss_score":  tss,
                    "atr_zone":   atr_zone,
                }
            })

            if runner_is_live:
                continue   # ← continue not return, so next symbol still runs

            async with httpx.AsyncClient() as client:
                status = await client.get(f"{BACKEND_URL}/api/bot/status", timeout=5)
                bot_running = status.json().get("running", False)

            if signal != "HOLD" and bot_running and atr_zone != "extreme" and tss >= 3:
                result = await execute_trade(symbol, signal, 1.0)
                print(f"[deriv:{cred.user_id}:{symbol}] trade result: {result}")

        except Exception as e:
            print(f"[signal_engine] deriv error [{cred.user_id}:{symbol}]: {e}")
# ============================================================
# STRATEGY RUNNER BOOT + WATCHDOG
# FIX 2: task is restarted automatically if it crashes.
# ============================================================

_strategy_runner_task: asyncio.Task | None = None


def _runner_is_alive() -> bool:
    return _strategy_runner_task is not None and not _strategy_runner_task.done()


async def _boot_strategy_runner(api_token: str) -> None:
    global _strategy_runner_task

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
        name="strategy-runner",
    )
    print(f"[signal_engine] StrategyRunner booted — balance={balance} ✓")


# ============================================================
# MAIN LOOP
# ============================================================

async def run_signal_loop():
    print("[signal_engine] starting...")

    # ── Initial credential fetch ──────────────────────────────
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Credentials))
        creds  = result.scalars().all()

    deriv_cred = next((c for c in creds if c.broker == "deriv"), None)

    # ── Boot runner on startup ────────────────────────────────
    if deriv_cred:
        api_token = decrypt(deriv_cred.password)
        await _boot_strategy_runner(api_token)

    # ── Main loop ─────────────────────────────────────────────
    while True:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Credentials))
            creds  = result.scalars().all()

        deriv_cred = next((c for c in creds if c.broker == "deriv"), None)

        # FIX 2: watchdog — restart runner if it crashed
        if deriv_cred and not _runner_is_alive():
            print("[signal_engine] StrategyRunner dead — restarting...")
            api_token = decrypt(deriv_cred.password)
            await _boot_strategy_runner(api_token)

        runner_live = _runner_is_alive()

        for cred in creds:
            if cred.broker == "deriv":
                await process_deriv_account(cred, runner_is_live=runner_live)

        await asyncio.sleep(30)