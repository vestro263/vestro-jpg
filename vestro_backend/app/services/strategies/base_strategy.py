"""
base_strategy.py
================
Every strategy in this folder MUST inherit BaseStrategy.

Enforces a standard interface so strategy_runner can call
any strategy without knowing its internals.

Pipeline:
    fetch → compute → log → broadcast → gate → execute → mark_executed
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

    # Override in child strategies
    NAME   = "unnamed"   # e.g. "V75", "Crash500"
    SYMBOL = "R_100"     # Deriv symbol

    def __init__(self, api_token: str, broadcast_fn, execute_trade_fn):
        self.api_token = api_token
        self.broadcast_fn = broadcast_fn
        self.execute_trade_fn = execute_trade_fn
        self.logger = logging.getLogger(f"strategy.{self.NAME}")

        # ML tracking
        self._last_log_id = None

    # ─────────────────────────────────────────────
    # REQUIRED METHODS (must be implemented)
    # ─────────────────────────────────────────────

    @abstractmethod
    async def fetch_market_data(self) -> dict:
        """
        Pull latest market data (ticks, candles, etc).

        Returns:
            dict → raw data needed for strategy logic
        """


    @abstractmethod
    async def compute_signal(self, market_data: dict) -> dict:
        """
        Run indicator logic on market_data.

        Must return:
        {
            "signal":     "BUY" | "SELL" | "HOLD",
            "symbol":     str,
            "confidence": float (0.0 - 1.0),
            "reason":     str,
            "amount":     float,
            "meta":       dict,
        }
        """
        ...

    @abstractmethod
    async def should_execute(self, signal: dict) -> bool:
        """
        Final gate before execution.

        Responsible for:
        - risk limits
        - cooldowns
        - max trades
        - open position checks

        Returns:
            bool → True if trade should execute
        """


    # ─────────────────────────────────────────────
    # CORE PIPELINE
    # ─────────────────────────────────────────────

    async def run(self):
        """
        Full execution pipeline (called by strategy_runner)

        Steps:
            1. Fetch market data
            2. Compute signal
            3. Log signal (ML dataset)
            4. Broadcast signal (frontend)
            5. Execution gate
            6. Execute trade
            7. Mark executed (for ML)
        """
        try:
            self.logger.info(f"[{self.NAME}] running pipeline...")

            # ── Step 1: Market Data
            market_data = await self.fetch_market_data()

            # ── Step 2: Compute Signal
            signal = await self.compute_signal(market_data)

            # Validate signal structure (safety)
            if not isinstance(signal, dict) or "signal" not in signal:
                raise ValueError(f"[{self.NAME}] Invalid signal format")

            # ── Step 3: Log Signal (ML dataset)
            try:
                from ml.signal_logger import log_signal
                self._last_log_id = await log_signal(
                    signal,
                    strategy_name=self.NAME
                )
            except Exception as e:
                self.logger.warning(f"[{self.NAME}] log_signal failed: {e}")
                self._last_log_id = None

            self.logger.info(
                f"[{self.NAME}] signal={signal['signal']} "
                f"confidence={signal.get('confidence', 0):.0%} "
                f"reason={signal.get('reason', '')}"
            )

            # ── Step 4: Broadcast (ALWAYS)
            try:
                await self.broadcast_fn({
                    "strategy": self.NAME,
                    "symbol": signal.get("symbol", self.SYMBOL),
                    "action": signal["signal"],
                    "signal": signal.get("signal_data", signal),
                })
            except Exception as e:
                self.logger.warning(f"[{self.NAME}] broadcast failed: {e}")

            # ── Step 5: Skip HOLD
            if signal["signal"] == "HOLD":
                return

            # ── Step 6: Execution Gate
            if not await self.should_execute(signal):
                self.logger.info(f"[{self.NAME}] execution blocked by gate")
                return

            # ── Step 7: Execute Trade
            self.logger.info(
                f"[{self.NAME}] EXECUTING {signal['signal']} "
                f"{signal.get('symbol', self.SYMBOL)} "
                f"amount={signal.get('amount')}"
            )

            result = await self.execute_trade_fn(
                symbol=signal.get("symbol", self.SYMBOL),
                action="rise" if signal["signal"] == "BUY" else "fall",
                amount=signal.get("amount", 1.0),
            )

            self.logger.info(f"[{self.NAME}] trade result: {result}")

            # ── Step 8: Mark Executed (ONLY if success)
            if result and result.get("status") in ("filled", "success"):
                if self._last_log_id:
                    try:
                        from  ml.signal_logger import mark_executed
                        await mark_executed(self._last_log_id)
                    except Exception as e:
                        self.logger.warning(f"[{self.NAME}] mark_executed failed: {e}")
            else:
                # Optional: track failed executions
                if self._last_log_id:
                    try:
                        from ml.signal_logger import mark_failed
                        await mark_failed(
                            self._last_log_id,
                            reason=result.get("error", "execution_failed") if result else "no_result"
                        )
                    except Exception:
                        pass

        except Exception as e:
            self.logger.error(f"[{self.NAME}] pipeline error: {e}", exc_info=True)