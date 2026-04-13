"""
signal_engine.py  (upgraded)
=============================
Orchestrates the full Vestro ML pipeline loop.

Changes vs original
--------------------
1. RETRAIN PIPELINE REPLACES run_validator
   run_validator() only ran walk-forward validation and wrote CalibrationConfig.
   run_retrain_pipeline() does the full sequence:
     feature_engineering → class_balancer → regime_detector →
     walk_forward_validate → calibrate → save versioned model → write DB
   Cadence unchanged: every 120 loops (~60 minutes at 30s sleep).

2. API TOKEN PASSED TO ML TASKS
   run_retrain_pipeline() needs the Deriv token to fetch candles for feature
   enrichment.  Token is decrypted once per boot and passed to all tasks.
   run_labeler() already accepted the token — no change there.

3. REGIME CACHE (module-level, read by strategies)
   _current_regimes: dict[symbol → RegimeLabel string] is refreshed every
   10 loops alongside the labeler run.  Strategies read it via:
       from app.signal_engine import get_current_regime
   This is fire-and-forget — failure leaves the previous regime cached.

4. GRACEFUL TASK REFERENCE MANAGEMENT
   All background tasks are stored in _background_tasks set so the GC
   can't cancel them mid-run (asyncio best practice for fire-and-forget).
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

from datetime import datetime
from .strategies.strategy_runner import StrategyRunner
from ml.calibration_loader import start_reload_loop, get_thresholds, set_cached_regime
from ml.outcome_labeler    import run_labeler
from ml.retrain            import run_retrain_pipeline          # ← replaces run_validator
from ml.regime_detector    import RegimeDetector, RegimeLabel   # ← new

DERIV_APP_ID    = os.environ["DERIV_APP_ID"]
BACKEND_URL     = os.environ.get("BACKEND_URL", "https://vestro-jpg.onrender.com")
_BOT_STATE_FILE = pathlib.Path("/tmp/vestro_bot_running.txt")

MIN_STAKE = 0.35
MAX_STAKE = 10.0

# ── Module-level regime cache (written by _refresh_regimes, read by strategies)
# Maps symbol string → RegimeLabel string, e.g. {"R_75": "TREND", "CRASH500": "RANGE"}
_current_regimes: dict[str, str] = {}

# ── Task reference set — prevents fire-and-forget tasks being GC'd mid-run ───
_background_tasks: set[asyncio.Task] = set()


def _is_bot_running() -> bool:
    try:
        return _BOT_STATE_FILE.read_text().strip() == "1"
    except Exception:
        return False


def get_current_regime(symbol: str) -> str:
    """
    Read the cached regime for a symbol.  Used by strategies at signal-fire time.

    Returns a RegimeLabel string: "TREND" | "RANGE" | "HIGH_VOL" | "CRASH" | "UNKNOWN"
    Falls back to "UNKNOWN" if no regime has been detected yet so strategies
    fail-open (they apply no regime gate) rather than suppressing all signals.

    Usage in v75_strategy.py compute_signal():
        from app.signal_engine import get_current_regime
        regime = get_current_regime(self.SYMBOL)
    """
    return _current_regimes.get(symbol, RegimeLabel.UNKNOWN.value)


def _fire_task(coro, name: str) -> asyncio.Task:
    """
    Schedule a coroutine as a fire-and-forget task.
    Stores the reference in _background_tasks so the GC can't cancel it.
    Automatically removes it from the set when done.
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


# =============================================================================
# Regime refresh  (called every 10 loops alongside labeler)
# =============================================================================

async def _refresh_regimes(api_token: str) -> None:
    """
    Fetch recent candles for each active symbol, run statistical regime
    detection, and update _current_regimes cache.

    Statistical detection is used here (no fit required) so this runs fast
    at signal-fire cadence without needing the clustering model.
    """
    from ml.feature_engineering  import fetch_candles, build_feature_df, GRANULARITY
    from ml.regime_detector      import detect_current_regime

    symbol_map = {"R_75": "R_75", "R_25": "R_25", "CRASH500": "CRASH500"}

    for symbol in symbol_map:
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
            _current_regimes[symbol] = regime.value
            set_cached_regime(symbol, regime.value)   # ← push into calibration_loader cache

            if prev != regime.value:
                print(
                    f"[signal_engine] regime change {symbol}: "
                    f"{prev} → {regime.value}"
                )
        except Exception as exc:
            # Non-fatal — leave previous regime cached
            print(f"[signal_engine] regime refresh failed for {symbol}: {exc}")


# =============================================================================
# Unchanged helpers
# =============================================================================

async def broadcast_to_frontend(data: dict):
    async with httpx.AsyncClient() as client:
        try:
            await client.post(f"{BACKEND_URL}/api/signal/broadcast", json=data, timeout=5)
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

    _loop_count = 0
    deriv_cred  = None
    _api_token  = None   # cached decrypted token — decrypt once, reuse

    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Credentials))
                creds  = result.scalars().all()

            print(f"[signal_engine] credentials found: {len(creds)}")

            # ── Demo / VRTC account ───────────────────────────────────
            deriv_cred = next(
                (c for c in creds if c.broker == "deriv" and c.user_id.startswith("VRTC")),
                None,
            )

            if deriv_cred:
                # Decrypt once and cache to avoid repeated crypto overhead
                if _api_token is None:
                    _api_token = decrypt(deriv_cred.password)

                if not _runner_is_alive():
                    print("[signal_engine] booting strategy runner...")
                    await _boot_strategy_runner(_api_token, deriv_cred.user_id)
                    _fire_task(start_reload_loop(), name="calibration-reload")

            if _runner_is_alive():
                print("[signal_engine] runner alive — execution delegated to strategies")

        except Exception as e:
            print(f"[signal_engine] loop error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            _loop_count += 1

            # ── Every 10 loops (~5 min): label outcomes + refresh regimes ──
            if _loop_count % 10 == 0 and _api_token:
                _fire_task(
                    run_labeler(_api_token),
                    name=f"outcome-labeler-{_loop_count}",
                )
                _fire_task(
                    _refresh_regimes(_api_token),
                    name=f"regime-refresh-{_loop_count}",
                )

            # ── Every 120 loops (~60 min): full retrain pipeline ─────────
            # Replaces the old run_validator() call.
            # run_retrain_pipeline:
            #   1. loads labeled rows from DB
            #   2. enriches with candle features (needs api_token)
            #   3. detects regimes + preserves crash rows
            #   4. balances classes
            #   5. walk-forward validates
            #   6. calibrates with Platt / Isotonic
            #   7. saves versioned model + writes CalibrationConfig to DB
            # calibration_loader.start_reload_loop() picks up the new DB
            # row within its next 30-minute refresh cycle.
            if _loop_count % 120 == 0 and _api_token:
                _fire_task(
                    run_retrain_pipeline(api_token=_api_token),
                    name=f"retrain-pipeline-{_loop_count}",
                )

        await asyncio.sleep(30)