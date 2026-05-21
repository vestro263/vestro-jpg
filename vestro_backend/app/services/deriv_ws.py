"""
deriv_ws.py
===========

Compatibility wrapper.

ALL routes/services should import from here.

Internally delegates to safe_deriv_ws.
"""

from app.services.safe_deriv_ws import (
    SafeDerivWS,
    fetch_balance,
    fetch_candles,
    get_account_info,
    get_mt5_login_list,
    DerivWSException,
    DerivAuthException,
    DerivDataException,
)

__all__ = [
    "SafeDerivWS",
    "fetch_balance",
    "fetch_candles",
    "get_account_info",
    "get_mt5_login_list",
    "DerivWSException",
    "DerivAuthException",
    "DerivDataException",
]