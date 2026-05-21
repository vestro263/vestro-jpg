import json
import asyncio
import websockets
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id={app_id}"


# ============================================================
# AUTH CONTEXT MANAGER (CORE FIXED VERSION)
# ============================================================
@asynccontextmanager
async def _authorized_ws(app_id: str, api_token: str):
    url = _WS_URL.format(app_id=app_id)

    logger.warning(
        f"[DERIV WS] CONNECTING app_id={app_id}"
    )

    async with websockets.connect(url) as ws:
        # AUTH
        await ws.send(json.dumps({
            "authorize": api_token
        }))

        auth = json.loads(await ws.recv())

        logger.warning(
            f"[DERIV WS] AUTH RESPONSE:\n"
            f"{json.dumps(auth, indent=2)}"
        )

        if auth.get("error"):
            raise ValueError(
                f"Deriv auth error: "
                f"{auth['error'].get('message')}"
            )

        loginid = auth.get("authorize", {}).get("loginid")

        logger.warning(
            f"[DERIV WS] AUTH SUCCESS loginid={loginid}"
        )

        yield ws


# ============================================================
# ACCOUNT INFO
# ============================================================
async def get_account_info(app_id: str, api_token: str) -> dict:
    url = _WS_URL.format(app_id=app_id)

    logger.warning(
        f"[ACCOUNT INFO] app_id={app_id}"
    )

    logger.warning(
        f"[ACCOUNT INFO] token_prefix={api_token[:12]}..."
    )

    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({
            "authorize": api_token
        }))

        auth = json.loads(await ws.recv())

        logger.warning(
            f"[ACCOUNT INFO] AUTH RESPONSE:\n"
            f"{json.dumps(auth, indent=2)}"
        )

        if auth.get("error"):
            return {
                "status": "error",
                "message": auth["error"]["message"],
            }

        a = auth["authorize"]

        balance = float(a.get("balance", 0))

        # Optional live balance refresh
        try:
            await ws.send(json.dumps({
                "balance": 1,
                "subscribe": 0
            }))

            bal_resp = json.loads(
                await asyncio.wait_for(ws.recv(), timeout=5)
            )

            if "balance" in bal_resp and "error" not in bal_resp:
                balance = float(
                    bal_resp["balance"]["balance"]
                )

        except Exception as e:
            logger.warning(
                f"[ACCOUNT INFO] balance refresh failed: {e}"
            )

        return {
            "account_id": a.get("loginid", ""),
            "balance": balance,
            "currency": a.get("currency", "USD"),
            "equity": balance,
            "profit": 0,
            "margin_free": balance,
            "name": a.get("fullname", ""),
            "email": a.get("email", ""),
            "is_virtual": a.get("is_virtual", 0) == 1,
            "leverage": 0,
        }


# ============================================================
# CONTRACT TYPE
# ============================================================
def _contract_type(action: str) -> str:
    return "CALL" if action.upper() in {"RISE", "BUY", "CALL"} else "PUT"


# ============================================================
# EXECUTE TRADE
# ============================================================
async def execute_trade(
    app_id: str,
    api_token: str,
    symbol: str,
    action: str,
    amount: float,
    duration: int = 5,
    duration_unit: str = "m",
) -> dict:

    contract_type = _contract_type(action)
    url = _WS_URL.format(app_id=app_id)

    async with websockets.connect(url) as ws:
        # AUTH
        await ws.send(json.dumps({
            "authorize": api_token
        }))

        auth = json.loads(await ws.recv())

        logger.warning(
            f"[TRADE AUTH] {json.dumps(auth)}"
        )

        if auth.get("error"):
            return {
                "status": "error",
                "message": auth["error"]["message"]
            }

        # PROPOSAL
        await ws.send(json.dumps({
            "proposal": 1,
            "amount": amount,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": duration,
            "duration_unit": duration_unit,
            "symbol": symbol,
        }))

        p_resp = json.loads(await ws.recv())

        if p_resp.get("error"):
            return {
                "status": "error",
                "message": p_resp["error"]["message"]
            }

        proposal_id = p_resp["proposal"]["id"]
        ask_price = p_resp["proposal"]["ask_price"]

        # BUY
        await ws.send(json.dumps({
            "buy": proposal_id,
            "price": ask_price
        }))

        b_resp = json.loads(await ws.recv())

        if b_resp.get("error"):
            return {
                "status": "error",
                "message": b_resp["error"]["message"]
            }

        c = b_resp["buy"]

        return {
            "contract_id": c["contract_id"],
            "transaction_id": c["transaction_id"],
            "buy_price": c["buy_price"],
            "payout": c["payout"],
            "contract_type": contract_type,
            "symbol": symbol,
            "longcode": c.get("longcode", ""),
        }


# ============================================================
# CONTRACT WATCHER
# ============================================================
async def watch_contract(
    app_id: str,
    api_token: str,
    contract_id: int,
    callback
):

    url = _WS_URL.format(app_id=app_id)

    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({
            "authorize": api_token
        }))

        auth = json.loads(await ws.recv())

        if auth.get("error"):
            return {
                "status": "error",
                "message": auth["error"]["message"]
            }

        await ws.send(json.dumps({
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe": 1,
        }))

        while True:
            msg = json.loads(await ws.recv())

            if msg.get("error"):
                break

            contract = msg.get("proposal_open_contract", {})

            if not contract:
                continue

            await callback({
                "contract_id": contract_id,
                "status": contract.get("status", "open"),
                "buy_price": contract.get("buy_price", 0),
                "bid_price": contract.get("bid_price", 0),
                "profit": contract.get("profit", 0),
                "profit_pct": contract.get("profit_percentage", 0),
                "entry_spot": contract.get("entry_spot", 0),
                "current_spot": contract.get("current_spot", 0),
                "expiry_time": contract.get("expiry_time", 0),
                "is_expired": contract.get("is_expired", False),
                "is_sold": contract.get("is_sold", False),
            })

            if contract.get("is_expired") or contract.get("is_sold"):
                break


# ============================================================
# LINKED ACCOUNTS
# ============================================================
async def get_linked_accounts(app_id: str, token: str) -> list[dict]:
    uri = _WS_URL.format(app_id=app_id)

    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({
            "authorize": token
        }))

        auth = json.loads(await ws.recv())

        logger.warning(
            f"[LINKED ACCOUNTS] AUTH:\n"
            f"{json.dumps(auth, indent=2)}"
        )

        if auth.get("error"):
            raise Exception(auth["error"]["message"])

        await ws.send(json.dumps({
            "account_list": 1
        }))

        resp = json.loads(await ws.recv())

        logger.warning(
            f"[LINKED ACCOUNTS] RESPONSE:\n"
            f"{json.dumps(resp, indent=2)}"
        )

        if resp.get("error"):
            raise Exception(resp["error"]["message"])

        all_accounts = resp.get("account_list", [])

        logger.warning(
            f"[LINKED ACCOUNTS] ACCOUNTS="
            f"{[a['loginid'] for a in all_accounts]}"
        )

        return [
            {
                "account_id": acc["loginid"],
                "token": token
            }
            for acc in all_accounts
            if not acc["loginid"].startswith(("VRW", "RW"))
        ]


# ============================================================
# MT5 LOGIN LIST
# ============================================================
async def get_mt5_login_list(app_id: str, token: str) -> list[dict]:
    url = _WS_URL.format(app_id=app_id)

    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({
            "authorize": token
        }))

        auth = json.loads(await ws.recv())

        if auth.get("error"):
            raise Exception(
                f"authorize failed: "
                f"{auth['error']['message']}"
            )

        await ws.send(json.dumps({
            "mt5_login_list": 1
        }))

        resp = json.loads(await ws.recv())

        if resp.get("error"):
            raise Exception(
                f"mt5_login_list failed: "
                f"{resp['error']['message']}"
            )

        return resp.get("mt5_login_list", [])