import asyncio
import httpx
import numpy as np
from sqlalchemy import select, text
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
from ml.walk_forward_validator import run_validator

DERIV_APP_ID    = os.environ["DERIV_APP_ID"]
BACKEND_URL     = os.environ.get("BACKEND_URL", "https://vestro-jpg.onrender.com")
_BOT_STATE_FILE = pathlib.Path("/tmp/vestro_bot_running.txt")

MIN_STAKE = 0.35
MAX_STAKE = 10.0


def _is_bot_running() -> bool:
    try:
        return _BOT_STATE_FILE.read_text().strip() == "1"
    except:
        return False


async def broadcast_to_frontend(data: dict):
    async with httpx.AsyncClient() as client:
        try:
            await client.post(f"{BACKEND_URL}/api/signal/broadcast", json=data, timeout=5)
        except Exception as e:
            print(f"[signal_engine] broadcast error: {e}")


async def execute_trade(
    symbol: str,
    action: str,
    amount: float,
    broker: str = "deriv",
    account_id: str = "",
) -> dict | None:
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(f"{BACKEND_URL}/api/trade", json={
                "broker":     broker,
                "symbol":     symbol,
                "action":     action,
                "amount":     amount,
                "account_id": account_id,
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


async def mark_signal_executed(signal_log_id: str) -> None:
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("""
                UPDATE signal_logs
                SET executed = true, executed_at = NOW()
                WHERE id = :id
            """), {"id": signal_log_id})
            await db.commit()
    except Exception as e:
        print(f"[signal_engine] mark_executed error: {e}")


_strategy_runner_task: asyncio.Task | None = None


def _runner_is_alive() -> bool:
    return _strategy_runner_task is not None and not _strategy_runner_task.done()


async def _boot_strategy_runner(api_token: str, account_id: str) -> None:
    global _strategy_runner_task
    balance = await fetch_deriv_balance(api_token)
    runner  = StrategyRunner(
        api_token        = api_token,
        balance          = balance,
        broadcast_fn     = broadcast_to_frontend,
        execute_trade_fn = execute_trade,
        is_prop          = False,
        account_id       = account_id,
    )
    _strategy_runner_task = asyncio.create_task(runner.start(), name="strategy-runner")
    print(f"[signal_engine] StrategyRunner booted — balance={balance} account_id={account_id} ✓")


async def run_signal_loop():
    print("[signal_engine] starting...")
    await asyncio.sleep(5)

    _loop_count = 0
    deriv_cred  = None

    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Credentials))
                creds  = result.scalars().all()

            print(f"[signal_engine] credentials found: {len(creds)}")

            deriv_cred = next((c for c in creds if c.broker == "deriv"), None)

            if deriv_cred and not _runner_is_alive():
                print("[signal_engine] booting strategy runner...")
                await _boot_strategy_runner(decrypt(deriv_cred.password), deriv_cred.user_id)
                asyncio.create_task(start_reload_loop(), name="calibration-reload")

            if _runner_is_alive():
                print(f"[signal_engine] runner alive — execution delegated to strategies")

        except Exception as e:
            print(f"[signal_engine] loop error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            _loop_count += 1

            if _loop_count % 10 == 0 and deriv_cred:
                asyncio.create_task(
                    run_labeler(decrypt(deriv_cred.password)),
                    name=f"outcome-labeler-{_loop_count}"
                )

            if _loop_count % 120 == 0:
                asyncio.create_task(
                    run_validator(),
                    name=f"calibration-trainer-{_loop_count}"
                )

        await asyncio.sleep(30)