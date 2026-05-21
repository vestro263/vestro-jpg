"""
safe_deriv_ws.py
================

Robust Deriv websocket helper for VESTRO.

Features
--------
✓ Safe reconnects
✓ Proper auth validation
✓ Timeout protection
✓ Render-safe websocket settings
✓ Heartbeat/ping support
✓ Structured exceptions
✓ Safe request-response API
✓ Candle fetching helper
✓ Balance fetching helper
✓ MT5 helper
✓ Account helper
"""

import asyncio
import json
import logging
import os
from typing import Any

import websockets
from websockets.exceptions import (
    ConnectionClosed,
    InvalidStatus,
)

logger = logging.getLogger(__name__)

DERIV_APP_ID = os.getenv("DERIV_APP_ID", "").strip()

DERIV_WS_BASE = os.getenv(
    "DERIV_WS_URL",
    "wss://ws.derivws.com/websockets/v3"
)

if not DERIV_APP_ID:
    raise RuntimeError(
        "DERIV_APP_ID environment variable missing"
    )


# ============================================================
# EXCEPTIONS
# ============================================================

class DerivWSException(Exception):
    pass


class DerivAuthException(Exception):
    pass


class DerivDataException(Exception):
    pass


# ============================================================
# SAFE WEBSOCKET CLIENT
# ============================================================

class SafeDerivWS:

    def __init__(
        self,
        api_token: str,
        retries: int = 3,
        timeout: int = 15,
    ):
        self.api_token = api_token
        self.retries = retries
        self.timeout = timeout
        self.ws = None
        self.auth_data = None

    @property
    def url(self):
        return f"{DERIV_WS_BASE}?app_id={DERIV_APP_ID}"

    async def connect(self):

        last_error = None

        for attempt in range(1, self.retries + 1):

            try:

                logger.info(
                    f"[DerivWS] connect "
                    f"{attempt}/{self.retries}"
                )

                self.ws = await websockets.connect(
                    self.url,
                    open_timeout=self.timeout,
                    close_timeout=self.timeout,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=10_000_000,
                )

                logger.info("[DerivWS] connected")

                await self.authorize()

                return self.ws

            except InvalidStatus as e:

                last_error = e

                logger.error(
                    f"[DerivWS] invalid status: {e}"
                )

                if "401" in str(e):

                    raise DerivAuthException(
                        "HTTP 401 from Deriv WS. "
                        "Check DERIV_APP_ID."
                    )

            except Exception as e:

                last_error = e

                logger.exception(
                    f"[DerivWS] connection failed: {e}"
                )

            await asyncio.sleep(
                min(attempt * 2, 10)
            )

        raise DerivWSException(
            f"Could not connect to Deriv WS: "
            f"{last_error}"
        )

    async def authorize(self):

        if not self.ws:
            raise DerivWSException(
                "WS not connected"
            )

        payload = {
            "authorize": self.api_token
        }

        await self.ws.send(
            json.dumps(payload)
        )

        raw = await asyncio.wait_for(
            self.ws.recv(),
            timeout=self.timeout,
        )

        response = json.loads(raw)

        if response.get("error"):

            code = response["error"].get("code")
            msg = response["error"].get("message")

            raise DerivAuthException(
                f"Authorize failed: "
                f"{code} - {msg}"
            )

        auth = response.get("authorize")

        if not auth:

            raise DerivAuthException(
                f"Missing authorize payload: "
                f"{response}"
            )

        self.auth_data = auth

        logger.info(
            f"[DerivWS] authorized "
            f"loginid={auth.get('loginid')} "
            f"balance={auth.get('balance')}"
        )

        return auth

    async def send(
        self,
        payload: dict[str, Any]
    ):

        if not self.ws:
            raise DerivWSException(
                "WS not connected"
            )

        await self.ws.send(
            json.dumps(payload)
        )

    async def recv(self):

        if not self.ws:
            raise DerivWSException(
                "WS not connected"
            )

        raw = await asyncio.wait_for(
            self.ws.recv(),
            timeout=self.timeout,
        )

        return json.loads(raw)

    async def request(
        self,
        payload: dict[str, Any]
    ):

        await self.send(payload)

        response = await self.recv()

        if response.get("error"):

            code = response["error"].get("code")
            msg = response["error"].get("message")

            raise DerivDataException(
                f"Deriv request failed: "
                f"{code} - {msg}"
            )

        return response

    async def ping(self):

        if not self.ws:
            raise DerivWSException(
                "WS not connected"
            )

        pong = await self.ws.ping()

        await asyncio.wait_for(
            pong,
            timeout=self.timeout,
        )

    async def close(self):

        try:

            if self.ws:
                await self.ws.close()

                logger.info(
                    "[DerivWS] closed"
                )

        except Exception:
            pass

        self.ws = None

    async def __aenter__(self):

        await self.connect()

        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        tb
    ):

        await self.close()


# ============================================================
# HIGH LEVEL HELPERS
# ============================================================

async def fetch_candles(
    api_token: str,
    symbol: str,
    count: int = 300,
    granularity: int = 60,
):

    async with SafeDerivWS(api_token) as deriv:

        response = await deriv.request({
            "ticks_history": symbol,
            "style": "candles",
            "granularity": granularity,
            "count": count,
            "end": "latest",
        })

        raw_candles = response.get("candles")

        if not raw_candles:

            raise DerivDataException(
                f"No candles returned for {symbol}"
            )

        candles = []

        for c in raw_candles:

            candles.append({
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": 60,
                "epoch": c.get("epoch", 0),
            })

        logger.info(
            f"[DerivWS] fetched "
            f"{len(candles)} candles "
            f"for {symbol}"
        )

        return candles


async def fetch_balance(
    api_token: str
):

    async with SafeDerivWS(api_token) as deriv:

        auth = deriv.auth_data or {}

        balance = auth.get("balance")

        if balance is None:
            return 0.0

        return float(balance)


async def get_account_info(
    api_token: str
):

    async with SafeDerivWS(api_token) as deriv:

        auth = deriv.auth_data or {}

        status_response = await deriv.request({
            "get_account_status": 1
        })

        return {
            "loginid": auth.get("loginid"),
            "balance": auth.get("balance"),
            "currency": auth.get("currency"),
            "country": auth.get("country"),
            "email": auth.get("email"),
            "is_virtual": auth.get("is_virtual"),
            "status": status_response.get(
                "get_account_status",
                {}
            ),
        }


async def get_mt5_login_list(
    api_token: str
):

    async with SafeDerivWS(api_token) as deriv:

        response = await deriv.request({
            "mt5_login_list": 1
        })

        return response.get(
            "mt5_login_list",
            []
        )


# ============================================================
# TRADE EXECUTION
# ============================================================

async def execute_trade(
    app_id: str,
    api_token: str,
    symbol: str,
    action: str,
    amount: float,
):
    """
    Execute Deriv Rise/Fall contract.
    """

    contract_type = "CALL" if action.lower() == "rise" else "PUT"

    async with SafeDerivWS(api_token) as deriv:

        proposal = await deriv.request({
            "proposal": 1,
            "amount": amount,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": 5,
            "duration_unit": "m",
            "symbol": symbol,
        })

        proposal_data = proposal.get("proposal")

        if not proposal_data:
            raise DerivDataException(
                f"No proposal returned: {proposal}"
            )

        proposal_id = proposal_data.get("id")

        buy_response = await deriv.request({
            "buy": proposal_id,
            "price": amount,
        })

        buy = buy_response.get("buy")

        if not buy:
            raise DerivDataException(
                f"Buy failed: {buy_response}"
            )

        logger.info(
            f"[DerivWS] trade executed "
            f"{symbol} {action} amount={amount}"
        )

        return {
            "status": "success",
            "contract_id": buy.get("contract_id"),
            "buy_price": buy.get("buy_price"),
            "payout": buy.get("payout"),
            "transaction_id": buy.get("transaction_id"),
            "contract_type": contract_type,
        }


# ============================================================
# CONTRACT WATCHER
# ============================================================

async def watch_contract(
    app_id: str,
    api_token: str,
    contract_id: int,
    on_update,
):
    """
    Subscribe to open contract updates.
    """

    async with SafeDerivWS(api_token) as deriv:

        await deriv.send({
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe": 1,
        })

        while True:

            try:

                response = await deriv.recv()

                poc = response.get(
                    "proposal_open_contract"
                )

                if not poc:
                    continue

                await on_update(poc)

                is_done = (
                    poc.get("is_expired")
                    or poc.get("is_sold")
                )

                if is_done:

                    logger.info(
                        f"[DerivWS] contract settled "
                        f"{contract_id}"
                    )

                    break

            except ConnectionClosed:

                logger.warning(
                    "[DerivWS] watch_contract disconnected"
                )

                break

            except Exception as e:

                logger.exception(
                    f"[DerivWS] watch_contract error: {e}"
                )

                break