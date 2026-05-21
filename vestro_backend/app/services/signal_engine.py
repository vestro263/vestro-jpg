"""
signal_engine.py
=============================

Upgraded debug + resilient demo detection version.

Key additions:
---------------
1. FULL credential debug logging
2. Detects demo accounts even if DB is wrong
3. Accepts:
      DOT...
      DT...
      VRTC...
4. Logs WHY accounts are rejected
5. Boots runners per account
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
from .regime_cache import (
    get_current_regime,
    set_current_regime as _set_regime,
)

from datetime import datetime

from .strategies.strategy_runner import StrategyRunner

from ml.calibration_loader import (
    start_reload_loop,
    get_thresholds,
    set_cached_regime,
)

from ml.outcome_labeler import run_labeler
from ml.retrain import run_retrain_pipeline
from ml.regime_detector import RegimeDetector, RegimeLabel


DERIV_APP_ID = os.environ["DERIV_APP_ID"]

BACKEND_URL = os.environ.get(
    "BACKEND_URL",
    "https://vestro-jpg.onrender.com",
)

_BOT_STATE_FILE = pathlib.Path("/tmp/vestro_bot_running.txt")

MIN_STAKE = 0.35
MAX_STAKE = 10.0

_current_regimes: dict[str, str] = {}

_background_tasks: set[asyncio.Task] = set()

# account_id -> runner task
_runner_tasks: dict[str, asyncio.Task] = {}

# account_id -> decrypted token
_token_cache: dict[str, str] = {}

_primary_api_token: str | None = None


# =============================================================================
# Helpers
# =============================================================================

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
# Demo account detection
# =============================================================================

def _is_demo_account(account_id: str, db_flag) -> bool:
    """
    Robust Deriv demo detection.

    Demo accounts:
        DOT...
        DT...
        VRTC...

    Real accounts:
        ROT...
        CR...
        MF...
    """

    acct = str(account_id or "").upper().strip()

    derived_demo = (
        acct.startswith("DOT")
        or acct.startswith("DT")
        or acct.startswith("VRTC")
    )

    return bool(db_flag) or derived_demo


# =============================================================================
# Regime refresh
# =============================================================================

async def _refresh_regimes(api_token: str) -> None:

    from ml.feature_engineering import (
        fetch_candles,
        build_feature_df,
        GRANULARITY,
    )

    from ml.regime_detector import detect_current_regime

    for symbol in ("R_75", "R_25", "CRASH500"):

        try:

            candles = await fetch_candles(
                symbol=symbol,
                granularity=GRANULARITY["M15"],
                count=50,
                api_token=api_token,
            )

            candle_df = build_feature_df(candles)

            regime = detect_current_regime(
                candle_df,
                lookback=20,
            )

            prev = _current_regimes.get(
                symbol,
                RegimeLabel.UNKNOWN.value,
            )

            _set_regime(symbol, regime.value)

            set_cached_regime(symbol, regime.value)

            if prev != regime.value:
                print(
                    f"[signal_engine] regime change "
                    f"{symbol}: {prev} → {regime.value}"
                )

        except Exception as exc:
            print(
                f"[signal_engine] regime refresh failed "
                f"for {symbol}: {exc}"
            )


# =============================================================================
# Broadcast
# =============================================================================

async def broadcast_to_frontend(data: dict):

    async with httpx.AsyncClient() as client:

        try:

            await client.post(
                f"{BACKEND_URL}/api/signal/broadcast",
                json=data,
                timeout=5,
            )

        except Exception as e:
            print(f"[signal_engine] broadcast error: {e}")


# =============================================================================
# Execute trade
# =============================================================================

async def execute_trade(
    symbol: str,
    action: str,
    amount: float,
    broker: str = "deriv",
    account_id: str = "",
) -> dict | None:

    async with httpx.AsyncClient() as client:

        try:

            res = await client.post(
                f"{BACKEND_URL}/api/trade",
                json={
                    "broker": broker,
                    "symbol": symbol,
                    "action": action,
                    "amount": amount,
                    "account_id": account_id,
                },
                timeout=10,
            )

            return res.json()

        except Exception as e:
            print(f"[signal_engine] trade error: {e}")
            return None


# =============================================================================
# Balance fetch
# =============================================================================

async def fetch_deriv_balance(api_token: str) -> float:

    url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"

    try:

        async with websockets.connect(url) as ws:

            await ws.send(json.dumps({
                "authorize": api_token
            }))

            auth = json.loads(await ws.recv())

            balance = float(auth["authorize"]["balance"])

            print(f"[signal_engine] live balance: {balance}")

            return balance

    except Exception as e:
        print(f"[signal_engine] balance fetch error: {e}")
        return 0.0


# =============================================================================
# Mark signal executed
# =============================================================================

async def mark_signal_executed(signal_log_id: str) -> None:

    try:

        async with AsyncSessionLocal() as db:

            await db.execute(text("""
                UPDATE signal_logs
                SET executed = true,
                    executed_at = NOW()
                WHERE id = :id
            """), {
                "id": signal_log_id
            })

            await db.commit()

    except Exception as e:
        print(f"[signal_engine] mark_executed error: {e}")


# =============================================================================
# Boot runner
# =============================================================================

async def _boot_runner_for_account(
    account_id: str,
    api_token: str,
) -> None:

    balance = await fetch_deriv_balance(api_token)

    async def _execute(
        symbol,
        action,
        amount,
        broker="deriv",
        **_,
    ):
        return await execute_trade(
            symbol=symbol,
            action=action,
            amount=amount,
            broker=broker,
            account_id=account_id,
        )

    runner = StrategyRunner(
        api_token=api_token,
        balance=balance,
        broadcast_fn=broadcast_to_frontend,
        execute_trade_fn=_execute,
        is_prop=False,
        account_id=account_id,
    )

    task = asyncio.create_task(
        runner.start(),
        name=f"strategy-runner-{account_id}",
    )

    _runner_tasks[account_id] = task

    print(
        f"[signal_engine] StrategyRunner booted "
        f"balance={balance} "
        f"account_id={account_id}"
    )


# =============================================================================
# MAIN LOOP
# =============================================================================

async def run_signal_loop():

    print("[signal_engine] starting...")

    await asyncio.sleep(5)

    _loop_count = 0
    _calibration_booted = False

    while True:

        try:

            # =============================================================
            # LOAD CREDS
            # =============================================================

            async with AsyncSessionLocal() as db:

                result = await db.execute(
                    select(Credentials)
                )

                creds = result.scalars().all()

            print(
                f"[signal_engine] credentials found: "
                f"{len(creds)}"
            )

            # =============================================================
            # FULL DEBUG
            # =============================================================

            for c in creds:

                try:

                    print(
                        "[signal_engine][cred] "
                        f"id={getattr(c, 'id', None)} | "
                        f"broker={repr(getattr(c, 'broker', None))} | "
                        f"account_id={repr(getattr(c, 'account_id', None))} | "
                        f"is_demo={repr(getattr(c, 'is_demo', None))} | "
                        f"user_id={repr(getattr(c, 'user_id', None))}"
                    )

                except Exception as debug_err:

                    print(
                        f"[signal_engine] credential debug failed: "
                        f"{debug_err}"
                    )

            # =============================================================
            # FILTER DEMO ACCOUNTS
            # =============================================================

            demo_creds = []

            for c in creds:

                try:

                    broker_ok = (
                        str(c.broker).strip().lower()
                        == "deriv"
                    )

                    account_ok = bool(c.account_id)

                    acct = str(
                        c.account_id or ""
                    ).upper().strip()

                    db_demo = bool(c.is_demo)

                    derived_demo = (
                        acct.startswith("DOT")
                        or acct.startswith("DT")
                        or acct.startswith("VRTC")
                    )

                    final_demo = db_demo or derived_demo

                    print(
                        "[signal_engine][filter] "
                        f"account={acct} | "
                        f"broker_ok={broker_ok} | "
                        f"db_demo={db_demo} | "
                        f"derived_demo={derived_demo} | "
                        f"final_demo={final_demo} | "
                        f"account_ok={account_ok}"
                    )

                    if broker_ok and final_demo and account_ok:

                        demo_creds.append(c)

                        print(
                            f"[signal_engine] "
                            f"DEMO ACCOUNT ACCEPTED: {acct}"
                        )

                except Exception as filter_err:

                    print(
                        f"[signal_engine] demo filter error: "
                        f"{filter_err}"
                    )

            print(
                f"[signal_engine] demo_creds count = "
                f"{len(demo_creds)}"
            )

            # =============================================================
            # BOOT RUNNERS
            # =============================================================

            if not demo_creds:

                print(
                    "[signal_engine] no demo credentials found "
                    "— runner will not boot until a demo "
                    "account is linked."
                )

            else:

                for cred in demo_creds:

                    account_id = cred.account_id

                    # decrypt once
                    if account_id not in _token_cache:

                        _token_cache[account_id] = decrypt(
                            cred.password
                        )

                    api_token = _token_cache[account_id]

                    # primary ML token
                    if _primary_api_token is None:
                        globals()["_primary_api_token"] = api_token

                    # boot runner
                    if not _runner_is_alive(account_id):

                        print(
                            f"[signal_engine] booting runner "
                            f"for account_id={account_id}"
                        )

                        await _boot_runner_for_account(
                            account_id,
                            api_token,
                        )

                    else:

                        print(
                            f"[signal_engine] runner alive "
                            f"account_id={account_id}"
                        )

                # calibration loop once
                if (
                    not _calibration_booted
                    and _primary_api_token
                ):

                    _fire_task(
                        start_reload_loop(),
                        name="calibration-reload",
                    )

                    _calibration_booted = True

        except Exception as e:

            print(f"[signal_engine] loop error: {e}")

            import traceback
            traceback.print_exc()

        finally:

            _loop_count += 1

            primary_token = globals().get(
                "_primary_api_token"
            )

            # every ~5 mins
            if _loop_count % 10 == 0 and primary_token:

                _fire_task(
                    run_labeler(primary_token),
                    name=f"outcome-labeler-{_loop_count}",
                )

                _fire_task(
                    _refresh_regimes(primary_token),
                    name=f"regime-refresh-{_loop_count}",
                )

            # every ~60 mins
            if _loop_count % 120 == 0 and primary_token:

                _fire_task(
                    run_retrain_pipeline(
                        api_token=primary_token
                    ),
                    name=f"retrain-pipeline-{_loop_count}",
                )

        await asyncio.sleep(30)