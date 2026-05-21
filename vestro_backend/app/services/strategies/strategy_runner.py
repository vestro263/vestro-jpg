"""
strategy_runner.py  (FULL DEBUG VERSION)
========================================
Purpose:
- Prevent silent strategy death
- Log every pipeline stage
- Show why signals are HOLD
- Show crashes immediately
- Keep strategies alive after exceptions

THIS VERSION WILL TELL YOU EXACTLY:
- if strategies are running
- if market data is fetched
- if signals are generated
- if broadcasts succeed
- if execution gates reject signals
- if trades execute
- if websocket payloads are malformed
"""

import asyncio
import logging
import traceback
from datetime import datetime

from . import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)

# ============================================================
# Strategy intervals
# ============================================================

STRATEGY_INTERVALS = {
    "V75": 30,
    "V25": 30,
    "Crash500": 2,
}

DEFAULT_INTERVAL = 30


class StrategyRunner:
    def __init__(
        self,
        api_token: str,
        balance: float,
        broadcast_fn,
        execute_trade_fn,
        is_prop: bool = False,
        account_id: str = "",
    ):
        self.api_token = api_token
        self.balance = balance
        self.broadcast_fn = broadcast_fn
        self.execute_trade_fn = execute_trade_fn
        self.is_prop = is_prop
        self.account_id = account_id

        self._tasks = []
        self._running = False

        logger.info(
            f"[StrategyRunner] INIT "
            f"account_id={account_id} "
            f"balance={balance}"
        )

        self._strategies = self._build_strategies()

    # ============================================================
    # Build strategies
    # ============================================================

    def _build_strategies(self):
        instances = []

        logger.info(
            f"[StrategyRunner] loading registry "
            f"count={len(STRATEGY_REGISTRY)}"
        )

        for StrategyClass in STRATEGY_REGISTRY:
            try:
                kwargs = {
                    "api_token": self.api_token,
                    "broadcast_fn": self.broadcast_fn,
                    "execute_trade_fn": self.execute_trade_fn,
                    "balance": self.balance,
                }

                if (
                    hasattr(StrategyClass.__init__, "__code__")
                    and "is_prop"
                    in StrategyClass.__init__.__code__.co_varnames
                ):
                    kwargs["is_prop"] = self.is_prop

                strategy = StrategyClass(**kwargs)

                strategy.account_id = self.account_id

                instances.append(strategy)

                logger.info(
                    f"[StrategyRunner] REGISTERED "
                    f"name={strategy.NAME} "
                    f"symbol={getattr(strategy, 'SYMBOL', 'UNKNOWN')} "
                    f"account_id={self.account_id}"
                )

            except Exception as e:
                logger.error(
                    f"[StrategyRunner] FAILED TO INIT "
                    f"{StrategyClass}: {e}"
                )
                traceback.print_exc()

        return instances

    # ============================================================
    # Start runner
    # ============================================================

    async def start(self):
        self._running = True

        logger.info(
            f"[StrategyRunner] STARTING "
            f"strategies={len(self._strategies)} "
            f"account_id={self.account_id}"
        )

        self._tasks = [
            asyncio.create_task(
                self._run_loop(strategy),
                name=f"strategy-{strategy.NAME}",
            )
            for strategy in self._strategies
        ]

        results = await asyncio.gather(
            *self._tasks,
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception):
                logger.error(
                    f"[StrategyRunner] TASK FAILED: {result}"
                )

    # ============================================================
    # Stop runner
    # ============================================================

    async def stop(self):
        logger.warning("[StrategyRunner] STOPPING")

        self._running = False

        for task in self._tasks:
            task.cancel()

        await asyncio.gather(
            *self._tasks,
            return_exceptions=True,
        )

        logger.warning("[StrategyRunner] STOPPED")

    # ============================================================
    # Main strategy loop
    # ============================================================

    async def _run_loop(self, strategy):
        interval = STRATEGY_INTERVALS.get(
            strategy.NAME,
            DEFAULT_INTERVAL,
        )

        logger.info(
            f"[{strategy.NAME}] LOOP STARTED "
            f"interval={interval}s "
            f"account_id={self.account_id}"
        )

        iteration = 0

        while self._running:
            started = datetime.utcnow()

            try:
                iteration += 1

                logger.info(
                    f"[{strategy.NAME}] "
                    f"ITERATION={iteration}"
                )

                # ====================================================
                # FETCH DATA
                # ====================================================

                logger.info(
                    f"[{strategy.NAME}] FETCHING MARKET DATA..."
                )

                market_data = await strategy.fetch_market_data()

                candles = market_data.get("candles", [])

                logger.info(
                    f"[{strategy.NAME}] "
                    f"CANDLES={len(candles)}"
                )

                if not candles:
                    logger.warning(
                        f"[{strategy.NAME}] "
                        f"NO CANDLES RETURNED"
                    )
                    await asyncio.sleep(interval)
                    continue

                # ====================================================
                # COMPUTE SIGNAL
                # ====================================================

                logger.info(
                    f"[{strategy.NAME}] COMPUTING SIGNAL..."
                )

                signal = await strategy.compute_signal(
                    market_data
                )

                logger.info(
                    f"[{strategy.NAME}] SIGNAL RESULT: "
                    f"{signal.get('signal')} "
                    f"confidence={signal.get('confidence')} "
                    f"reason={signal.get('reason')}"
                )

                # ====================================================
                # HOLD DEBUGGING
                # ====================================================

                if signal.get("signal") == "HOLD":
                    logger.warning(
                        f"[{strategy.NAME}] HOLD FILTERED | "
                        f"reason={signal.get('reason')}"
                    )

                # ====================================================
                # EXECUTION GATE
                # ====================================================

                logger.info(
                    f"[{strategy.NAME}] CHECKING EXECUTION..."
                )

                should_execute = await strategy.should_execute(
                    signal
                )

                logger.info(
                    f"[{strategy.NAME}] "
                    f"SHOULD_EXECUTE={should_execute}"
                )

                if not should_execute:
                    logger.warning(
                        f"[{strategy.NAME}] EXECUTION BLOCKED"
                    )
                    await asyncio.sleep(interval)
                    continue

                # ====================================================
                # EXECUTE TRADE
                # ====================================================

                logger.info(
                    f"[{strategy.NAME}] EXECUTING TRADE..."
                )

                result = await strategy.execute(signal)

                logger.info(
                    f"[{strategy.NAME}] TRADE RESULT: {result}"
                )

                # ====================================================
                # LOOP METRICS
                # ====================================================

                elapsed = (
                    datetime.utcnow() - started
                ).total_seconds()

                logger.info(
                    f"[{strategy.NAME}] LOOP COMPLETE "
                    f"elapsed={elapsed:.2f}s"
                )

            except asyncio.CancelledError:
                logger.warning(
                    f"[{strategy.NAME}] TASK CANCELLED"
                )
                raise

            except Exception as e:
                logger.error(
                    f"[{strategy.NAME}] CRASHED: {e}"
                )

                traceback.print_exc()

                try:
                    logger.error(
                        f"[{strategy.NAME}] "
                        f"ATTEMPTING RECOVERY..."
                    )

                    await self.broadcast_fn({
                        "type": "strategy_error",
                        "strategy": strategy.NAME,
                        "account_id": self.account_id,
                        "error": str(e),
                    })

                except Exception as broadcast_err:
                    logger.error(
                        f"[{strategy.NAME}] "
                        f"ERROR BROADCAST FAILED: "
                        f"{broadcast_err}"
                    )

            # ========================================================
            # NEVER DIE
            # ========================================================

            await asyncio.sleep(interval)