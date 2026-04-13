"""
regime_cache.py
===============
Thin module-level cache for the current market regime per symbol.
Lives outside signal_engine so strategies can import it without
creating a circular dependency:

    signal_engine -> strategy_runner -> v75_strategy -> regime_cache  (OK)
    signal_engine -> regime_cache                                      (OK)

signal_engine writes via set_current_regime().
Strategies read via get_current_regime().
"""

_current_regimes: dict[str, str] = {}

UNKNOWN = "UNKNOWN"


def get_current_regime(symbol: str) -> str:
    """
    Return cached regime for symbol.
    Falls back to UNKNOWN so strategies fail-open (no gate applied).
    """
    return _current_regimes.get(symbol, UNKNOWN)


def set_current_regime(symbol: str, regime: str) -> None:
    _current_regimes[symbol] = regime