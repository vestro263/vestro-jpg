"""
signal_engine.py  (upgraded)
=============================
Key changes:
1. Trades on ALL linked demo accounts, not just the first one found.
   Each user gets their own StrategyRunner instance so profits land
   on every connected account simultaneously.
2. Runner keyed by account_id — if a new account connects mid-session
   it gets its own runner on the next loop iteration.
3. Everything else unchanged.
"""

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
from .regime_cache import get_current_regime, set_current_regime as _set_regime

from datetime import datetime
from .strategies.strategy_runner import StrategyRunner
from ml.calibration_loader import start_reload_loop, get_thresholds, set_cached_regime
from ml.outcome_labeler    import run_labeler
from ml.retrain            import run_retrain_pipeline
from ml.regime_detector    import RegimeDetector, RegimeLabel

DERIV_APP_ID    = os.environ["DERIV_APP_ID"]
BACKEND_URL     = os.environ.get("BACKEND_URL", "https://vestro-jpg.onrender.com")
_BOT_STATE_FILE = pathlib.Path("/tmp/vestro_bot_running.txt")

MIN_STAKE = 0.35
MAX_STAKE = 10.0

_current_regimes:  dict[str, str]            = {}
_background_tasks: set[asyncio.Task]         = set()

# ── Per-account runner registry ───────────────────────────────────────────────
# Maps account_id → asyncio.Task
_runner_tasks: dict[str, asyncio.Task] = {}

# ── Per-account decrypted token cache ────────────────────────────────────────
# Maps account_id → decrypted api token
_token_cache: dict[str, str] = {}

# ── Primary token for ML tasks (labeler, regime, retrain) ────────────────────
_primary_api_token: str | None = None


def _is_bot_running() -> bool:
    try:
        return _BOT_STATE_FILE.read_text().strip() == "1"
    except Exception:
        return False


def _fire_task(coro, name: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _runner_is_alive(account_id: str) -> bool:
    task = _runner_tasks.get(account_id)
    return task is not None and not task.done()


# =============================================================================
# Regime refresh
# =============================================================================

async def _refresh_regimes(api_token: str) -> None:
    from ml.feature_engineering import fetch_candles, build_feature_df, GRANULARITY
    from ml.regime_detector     import detect_current_regime

    for symbol in ("R_75", "R_25", "CRASH500"):
        try:
            candles   = await fetch_candles(
                symbol      = symbol,
                granularity = GRANULARITY["M15"],
                count       = 50,
                api_token   = api_token,
            )
            candle_df = build_feature_df(candles)
            regime    = detect_current_regime(candle_df, lookback=20)

            prev = _current_regimes.get(symbol, RegimeLabel.UNKNOWN.value)
            _set_regime(symbol, regime.value)
            set_cached_regime(symbol, regime.value)

            if prev != regime.value:
                print(
                    f"[signal_engine] regime change {symbol}: "
                    f"{prev} → {regime.value}"
                )
        except Exception as exc:
            print(f"[signal_engine] regime refresh failed for {symbol}: {exc}")


# =============================================================================
# Helpers
# =============================================================================

async def broadcast_to_frontend(data: dict):
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{BACKEND_URL}/api/signal/broadcast", json=data, timeout=5
            )
        except Exception as e:
            print(f"[signal_engine] broadcast error: {e}")


async def execute_trade(
    symbol:     str,
    action:     str,
    amount:     float,
    broker:     str = "deriv",
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


# =============================================================================
# Boot one runner per account
# =============================================================================

async def _boot_runner_for_account(account_id: str, api_token: str) -> None:
    """Boot a StrategyRunner for a single account and register the task."""
    balance = await fetch_deriv_balance(api_token)

    # Each runner gets its own execute_trade closure bound to its account_id
    async def _execute(symbol, action, amount, broker="deriv", **_):
        return await execute_trade(
            symbol     = symbol,
            action     = action,
            amount     = amount,
            broker     = broker,
            account_id = account_id,
        )

    runner = StrategyRunner(
        api_token        = api_token,
        balance          = balance,
        broadcast_fn     = broadcast_to_frontend,
        execute_trade_fn = _execute,
        is_prop          = False,
        account_id       = account_id,
    )

    task = asyncio.create_task(runner.start(), name=f"strategy-runner-{account_id}")
    _runner_tasks[account_id] = task
    print(
        f"[signal_engine] StrategyRunner booted — "
        f"balance={balance} account_id={account_id} ✓"
    )


# =============================================================================
# Main loop
# =============================================================================

async def run_signal_loop():
    print("[signal_engine] starting...")
    await asyncio.sleep(5)

    _loop_count         = 0
    _calibration_booted = False

    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Credentials))
                creds  = result.scalars().all()

            print(f"[signal_engine] credentials found: {len(creds)}")

            # ── Collect ALL demo credentials across all users ─────────────
            demo_creds = [
                c for c in creds
                if c.broker == "deriv"
                and c.is_demo
                and c.account_id
            ]

            if not demo_creds:
                print(
                    f"[signal_engine] no demo credentials found — "
                    f"runner will not boot until a demo account is linked."
                )
            else:
                for cred in demo_creds:
                    account_id = cred.account_id

                    # Decrypt token once and cache it
                    if account_id not in _token_cache:
                        _token_cache[account_id] = decrypt(cred.password)

                    api_token = _token_cache[account_id]

                    # Set primary token for ML tasks (use first demo found)
                    if _primary_api_token is None:
                        import sys
                        # Hack to set module-level from inside loop
                        globals()["_primary_api_token"] = api_token

                    # Boot runner if not already running for this account
                    if not _runner_is_alive(account_id):
                        print(
                            f"[signal_engine] booting runner for "
                            f"account_id={account_id}..."
                        )
                        await _boot_runner_for_account(account_id, api_token)

                    else:
                        print(
                            f"[signal_engine] runner alive — "
                            f"account_id={account_id} execution delegated to strategies"
                        )

                # Boot calibration reload loop once
                if not _calibration_booted and _primary_api_token:
                    _fire_task(start_reload_loop(), name="calibration-reload")
                    _calibration_booted = True

        except Exception as e:
            print(f"[signal_engine] loop error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            _loop_count += 1
            primary_token = globals().get("_primary_api_token")

            # Every 10 loops (~5 min): label outcomes + refresh regimes
            if _loop_count % 10 == 0 and primary_token:
                _fire_task(
                    run_labeler(primary_token),
                    name=f"outcome-labeler-{_loop_count}",
                )
                _fire_task(
                    _refresh_regimes(primary_token),
                    name=f"regime-refresh-{_loop_count}",
                )

            # Every 120 loops (~60 min): full retrain pipeline
            if _loop_count % 120 == 0 and primary_token:
                _fire_task(
                    run_retrain_pipeline(api_token=primary_token),
                    name=f"retrain-pipeline-{_loop_count}",
                )

        await asyncio.sleep(30)