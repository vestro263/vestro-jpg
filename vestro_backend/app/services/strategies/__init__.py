"""
strategies/__init__.py
======================
Registry of all active strategies.

To add a new strategy in future:
  1. Create  strategies/my_new_strategy.py  (inherit BaseStrategy)
  2. Import and add it to STRATEGY_REGISTRY below
  3. Done — strategy_runner picks it up automatically
"""

from .v75_strategy     import V75Strategy
from .crash500_strategy import Crash500Strategy

# ── Add new strategies here ───────────────────────────────────
STRATEGY_REGISTRY = [
    V75Strategy,
    Crash500Strategy,
]