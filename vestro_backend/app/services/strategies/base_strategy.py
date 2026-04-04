"""
base_strategy.py
================
Every strategy in this folder MUST inherit BaseStrategy.
Enforces a standard interface so strategy_runner can call
any strategy without knowing its internals.
"""

from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """
    Standard contract every strategy must implement.

    signal_engine.py → strategy_runner.py → [each strategy]
                                             must follow this interface
    """

    # Set these in each strategy class
    NAME   = "unnamed"      # e.g. "V75" or "Crash500"
    SYMBOL = "R_100"        # Deriv symbol this strategy trades

    def __init__(self, api_token: str, broadcast_fn, execute_trade_fn):
        self.api_token = api_token
        self.broadcast_fn = broadcast_fn  # ← was self.broadcast
        self.execute_trade_fn = execute_trade_fn  # ← was self.execute_trade
        self.logger = logging.getLogger(f"strategy.{self.NAME}")


    @abstractmethod
    async def fetch_market_data(self) -> dict:
        """
        Pull latest market data (ticks, candles, etc).
        Returns a dict of raw data the strategy needs.
        """
        ...

    @abstractmethod
    async def compute_signal(self, market_data: dict) -> dict:
        """
        Run indicator logic on market_data.
        Must return a signal dict with at least:
        {
            "signal":     "BUY" | "SELL" | "HOLD",
            "symbol":     str,
            "confidence": float (0.0 - 1.0),
            "reason":     str,
            "amount":     float,   # lot/stake size
            "meta":       dict,    # any extra data to broadcast
        }
        """
        ...

    @abstractmethod
    async def should_execute(self, signal: dict) -> bool:
        """
        Final gate before execution.
        Check bot status, risk limits, open trades, cooldowns, etc.
        Return True only if trade should fire.
        """
        ...

    async def run(self):
        """
        Full pipeline called by strategy_runner on every tick/interval:
          1. fetch_market_data()
          2. compute_signal()
          3. broadcast signal to frontend (always)
          4. should_execute() gate
          5. execute_trade() if gate passes
        """
        try:
            self.logger.info(f"[{self.NAME}] running pipeline...")

            # Step 1 — market data
            market_data = await self.fetch_market_data()

            # Step 2 — signal
            signal = await self.compute_signal(market_data)
            self.logger.info(
                f"[{self.NAME}] signal={signal['signal']} "
                f"confidence={signal.get('confidence', 0):.0%} "
                f"reason={signal.get('reason', '')}"
            )

            # Step 3 — always broadcast
            await self.broadcast_fn({
                "strategy": self.NAME,
                "symbol": signal["symbol"],
                "action": signal["signal"],
                "signal": signal.get("signal_data", signal),
            })

            # Step 4 — execution gate
            if signal["signal"] == "HOLD":
                return

            if not await self.should_execute(signal):
                self.logger.info(f"[{self.NAME}] execution blocked by gate")
                return

            # Step 5 — fire trade
            self.logger.info(
                f"[{self.NAME}] EXECUTING {signal['signal']} "
                f"{signal['symbol']} amount={signal['amount']}"
            )
            # Step 5 — fire trade
            result = await self.execute_trade_fn(
                symbol=signal["symbol"],
                action="rise" if signal["signal"] == "BUY" else "fall",
                amount=signal["amount"],
            )
            self.logger.info(f"[{self.NAME}] trade result: {result}")

        except Exception as e:
            self.logger.error(f"[{self.NAME}] pipeline error: {e}")