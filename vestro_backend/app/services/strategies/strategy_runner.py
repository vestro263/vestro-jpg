"""
strategy_runner.py
==================
Loads every strategy from the registry and runs them
in parallel asyncio tasks.

signal_engine.py calls:
    from .strategies.strategy_runner import StrategyRunner
    runner = StrategyRunner(api_token, balance, broadcast_fn, execute_trade_fn, account_id=account_id)
    await runner.start()
"""

import asyncio
import logging

from . import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)

# How often each strategy's pipeline runs (seconds)
STRATEGY_INTERVALS = {
    "V75":      30,
    "V25":      30,   # same cadence as V75 — indicator-based
    "Crash500":  2,
}

DEFAULT_INTERVAL = 30


class StrategyRunner:
    """
    Boots all registered strategies in parallel.
    Each strategy runs on its own async loop at its own interval.
    They never block each other.
    """

    def __init__(
        self,
        api_token: str,
        balance: float,
        broadcast_fn,
        execute_trade_fn,
        is_prop: bool = False,
        account_id: str = "",        # ← FIX: accept account_id so it flows down to every strategy
    ):
        self.api_token        = api_token
        self.balance          = balance
        self.broadcast_fn     = broadcast_fn
        self.execute_trade_fn = execute_trade_fn
        self.is_prop          = is_prop
        self.account_id       = account_id   # ← FIX: store it
        self._tasks           = []
        self._running         = False

        # Instantiate every registered strategy
        self._strategies = self._build_strategies()

    def _build_strategies(self) -> list:
        instances = []
        for StrategyClass in STRATEGY_REGISTRY:
            try:
                # Pass extra kwargs only if the strategy accepts them
                kwargs = dict(
                    api_token        = self.api_token,
                    broadcast_fn     = self.broadcast_fn,
                    execute_trade_fn = self.execute_trade_fn,
                    balance          = self.balance,
                )
                # V75 also accepts is_prop
                if hasattr(StrategyClass.__init__, "__code__") and \
                   "is_prop" in StrategyClass.__init__.__code__.co_varnames:
                    kwargs["is_prop"] = self.is_prop

                instance = StrategyClass(**kwargs)

                # ← FIX: inject account_id into every strategy instance
                instance.account_id = self.account_id

                instances.append(instance)
                logger.info(f"[StrategyRunner] registered: {StrategyClass.NAME} account_id={self.account_id}")
            except Exception as e:
                logger.error(f"[StrategyRunner] failed to init {StrategyClass}: {e}")
        return instances

    async def start(self):
        """Launch all strategies as parallel asyncio tasks."""
        self._running = True
        logger.info(f"[StrategyRunner] starting {len(self._strategies)} strategies in parallel")

        self._tasks = [
            asyncio.create_task(
                self._run_loop(strategy),
                name=f"strategy-{strategy.NAME}"
            )
            for strategy in self._strategies
        ]

        # Run until cancelled
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self):
        """Gracefully cancel all running strategy tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("[StrategyRunner] all strategies stopped")

    async def _run_loop(self, strategy):
        """
        Infinite loop for a single strategy.
        Calls strategy.run() on its own interval, independently.
        """
        interval = STRATEGY_INTERVALS.get(strategy.NAME, DEFAULT_INTERVAL)
        logger.info(f"[{strategy.NAME}] loop started — interval={interval}s")

        while self._running:
            await strategy.run()
            await asyncio.sleep(interval)