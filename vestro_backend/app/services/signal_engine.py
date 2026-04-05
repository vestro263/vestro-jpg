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
import pathlib
from ml.signal_log_model import SignalLog

from datetime import datetime
from .strategies.strategy_runner import StrategyRunner
from ml.calibration_loader import start_reload_loop, get_thresholds
from ml.outcome_labeler    import run_labeler
from ml.calibration_trainer import run_trainer

DERIV_APP_ID    = os.environ["DERIV_APP_ID"]
BACKEND_URL     = os.environ.get("BACKEND_URL", "https://vestro-jpg.onrender.com")
_BOT_STATE_FILE = pathlib.Path("/tmp/vestro_bot_running.txt")


def _is_bot_running() -> bool:
    try:
        return _BOT_STATE_FILE.read_text().strip() == "1"
    except:
        return False


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


async def fetch_deriv_balance(api_token: str) -> float:
    url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"authorize": api_token}))
            auth    = json.loads(await ws.recv())
            balance = float(auth["authorize"]["balance"])
            print(f"[signal_engine] live balance: {balance}")
            return balance
    except Exception as e:
        print(f"[signal_engine] balance fetch error: {e}")
        return 0.0


# ============================================================
# MAIN SIGNAL LOOP
# ============================================================

async def process_deriv_account(cred, runner_is_live: bool = False):
    for symbol in ["R_75"]:
        try:
            api_token = decrypt(cred.password)
            closes    = list(await fetch_deriv_ticks(api_token, symbol))

            rsi      = compute_rsi(closes)
            ma_fast  = compute_ma(closes, 5)
            ma_slow  = compute_ma(closes, 20)
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
            tss += 1  # base point

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

            macd_bullish = macd_val > 0
            macd_bearish = macd_val < 0

            if rsi < 50 and macd_bullish:
                signal = "BUY"
            elif rsi > 50 and macd_bearish:
                signal = "SELL"
            else:
                signal = "HOLD"

            bot_running = _is_bot_running()

            print(f"[deriv:{cred.user_id}:{symbol}] RSI={rsi:.1f} TSS={tss}/5 "
                  f"ATR={atr_zone} MACD={'bull' if macd_bullish else 'bear'} "
                  f"signal={signal} bot={bot_running} "
                  f"runner={'live' if runner_is_live else 'off'}")

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

            # Save signal to DB for ML labeling
            try:


                entry_price = closes[-1]
                atr_val_now = atr_val
                direction = 1 if signal == "BUY" else (-1 if signal == "SELL" else 0)

                tp = entry_price + atr_val_now if direction == 1 else entry_price - atr_val_now
                sl = entry_price - atr_val_now if direction == 1 else entry_price + atr_val_now

                async with AsyncSessionLocal() as db:
                    db.add(SignalLog(
                        strategy="V75",
                        symbol=symbol,
                        signal=signal,
                        direction=direction,
                        entry_price=entry_price,
                        tp_price=tp if signal != "HOLD" else None,
                        sl_price=sl if signal != "HOLD" else None,
                        rsi=round(rsi, 2),
                        adx=round(adx_val, 2),
                        atr=round(atr_val, 5),
                        ema_50=round(ema50, 4),
                        ema_200=round(ema200, 4),
                        macd_hist=round(macd_val, 5),
                        tss_score=tss,
                        atr_zone=atr_zone,
                        confidence=0.0,
                        captured_at=datetime.utcnow(),
                    ))
                    await db.commit()
            except Exception as log_err:
                print(f"[signal_engine] SignalLog insert error: {log_err}")

            if runner_is_live:
                continue

            if signal != "HOLD" and bot_running and atr_zone != "extreme" and tss >= 3:
                print(f"[deriv:{cred.user_id}:{symbol}] EXECUTING {signal}")
                result = await execute_trade(symbol, signal, 1.0)
                print(f"[deriv:{cred.user_id}:{symbol}] trade result: {result}")

        except Exception as e:
            print(f"[signal_engine] deriv error [{cred.user_id}:{symbol}]: {e}")


# ============================================================
# STRATEGY RUNNER BOOT + WATCHDOG
# ============================================================

_strategy_runner_task: asyncio.Task | None = None


def _runner_is_alive() -> bool:
    return _strategy_runner_task is not None and not _strategy_runner_task.done()


async def _boot_strategy_runner(api_token: str) -> None:
    global _strategy_runner_task
    balance = await fetch_deriv_balance(api_token)
    runner  = StrategyRunner(
        api_token        = api_token,
        balance          = balance,
        broadcast_fn     = broadcast_to_frontend,
        execute_trade_fn = execute_trade,
        is_prop          = False,
    )
    _strategy_runner_task = asyncio.create_task(runner.start(), name="strategy-runner")
    print(f"[signal_engine] StrategyRunner booted — balance={balance} ✓")

async def run_signal_loop():
    print("[signal_engine] starting...")
    await asyncio.sleep(5)

    _loop_count = 0
    deriv_cred = None  # ← initialize here so it's always defined

    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Credentials))
                creds  = result.scalars().all()

            print(f"[signal_engine] credentials found: {len(creds)}")

            deriv_cred = next((c for c in creds if c.broker == "deriv"), None)

            if deriv_cred and not _runner_is_alive():
                print("[signal_engine] booting strategy runner...")
                await _boot_strategy_runner(decrypt(deriv_cred.password))
                asyncio.create_task(start_reload_loop(), name="calibration-reload")

            runner_live = _runner_is_alive()

            for cred in creds:
                if cred.broker == "deriv":
                    await process_deriv_account(cred, runner_is_live=runner_live)

        except Exception as e:
            print(f"[signal_engine] loop error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            _loop_count += 1

            # Label outcomes every 5 min
            if _loop_count % 10 == 0 and deriv_cred:
                asyncio.create_task(
                    run_labeler(decrypt(deriv_cred.password)),
                    name=f"outcome-labeler-{_loop_count}"
                )

            # Train every hour
            if _loop_count % 120 == 0:
                asyncio.create_task(
                    run_trainer(),
                    name=f"calibration-trainer-{_loop_count}"
                )

        await asyncio.sleep(30)