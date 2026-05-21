"""
safe_deriv_ws.py
================

Robust Deriv websocket helper for VESTRO strategies.

Fixes:
-------
✓ Handles HTTP 401 properly
✓ Validates authorize response
✓ Uses modern Deriv WS endpoint
✓ Retries automatically
✓ Adds ping/heartbeat timeout safety
✓ Prevents infinite crash loops
✓ Gives clean structured errors
✓ Works on Render reliably
✓ Safe candle fetching wrapper

Usage:
-------
from app.services.safe_deriv_ws import fetch_candles

candles = await fetch_candles(
    api_token=self.api_token,
    symbol="R_25",
    count=300,
)

Environment:
-------------
DERIV_APP_ID=xxxx
DERIV_WS_URL=wss://ws.derivws.com/websockets/v3
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


class DerivWSException(Exception):
    pass


class DerivAuthException(Exception):
    pass


class DerivDataException(Exception):
    pass


class SafeDerivWS:
    """
    Safe websocket manager with:
    - retries
    - auth validation
    - heartbeat timeout
    - clean logging
    """

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

    @property
    def url(self):
        return f"{DERIV_WS_BASE}?app_id={DERIV_APP_ID}"

    async def connect(self):
        """
        Establish websocket safely.
        """

        last_error = None

        for attempt in range(1, self.retries + 1):

            try:
                logger.info(
                    f"[DerivWS] connecting attempt "
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
                    f"[DerivWS] HTTP failure: {e}"
                )

                if "401" in str(e):
                    raise DerivAuthException(
                        "Deriv websocket rejected connection "
                        "(HTTP 401). "
                        "Check DERIV_APP_ID."
                    )

            except Exception as e:
                last_error = e

                logger.exception(
                    f"[DerivWS] connect failed: {e}"
                )

            await asyncio.sleep(min(attempt * 2, 10))

        raise DerivWSException(
            f"Failed websocket connection after retries: "
            f"{last_error}"
        )

    async def authorize(self):
        """
        Validate token properly.
        """

        if not self.ws:
            raise DerivWSException(
                "Websocket not connected"
            )

        payload = {
            "authorize": self.api_token
        }

        await self.ws.send(json.dumps(payload))

        raw = await asyncio.wait_for(
            self.ws.recv(),
            timeout=self.timeout,
        )

        response = json.loads(raw)

        if response.get("error"):

            code = response["error"].get("code")
            msg = response["error"].get("message")

            raise DerivAuthException(
                f"Authorize failed: {code} - {msg}"
            )

        auth = response.get("authorize")

        if not auth:
            raise DerivAuthException(
                f"Missing authorize payload: {response}"
            )

        logger.info(
            f"[DerivWS] authorized "
            f"account={auth.get('loginid')} "
            f"balance={auth.get('balance')}"
        )

        return auth

    async def send(self, payload: dict[str, Any]):
        """
        Safe send helper.
        """

        if not self.ws:
            raise DerivWSException(
                "Websocket not connected"
            )

        await self.ws.send(json.dumps(payload))

    async def recv(self):
        """
        Safe receive helper.
        """

        if not self.ws:
            raise DerivWSException(
                "Websocket not connected"
            )

        raw = await asyncio.wait_for(
            self.ws.recv(),
            timeout=self.timeout,
        )

        return json.loads(raw)

    async def request(self, payload: dict[str, Any]):
        """
        Request-response helper.
        """

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

    async def close(self):

        try:
            if self.ws:
                await self.ws.close()
                logger.info("[DerivWS] closed")
        except Exception:
            pass

        self.ws = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb):
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
    """
    Fetch candles safely from Deriv.
    """

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


async def fetch_balance(api_token: str):
    """
    Fetch account balance safely.
    """

    async with SafeDerivWS(api_token) as deriv:

        response = await deriv.request({
            "balance": 1
        })

        balance = (
            response
            .get("balance", {})
            .get("balance")
        )

        return float(balance)


# ============================================================
# QUICK TEST
# ============================================================

if __name__ == "__main__":

    async def main():

        token = os.getenv("DERIV_TOKEN")

        if not token:
            raise RuntimeError(
                "DERIV_TOKEN missing"
            )

        candles = await fetch_candles(
            api_token=token,
            symbol="R_25",
            count=10,
        )

        print()
        print("SUCCESS")
        print(candles[-1])

    asyncio.run(main())