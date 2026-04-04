import json
import websockets

_WS_URL = "wss://ws.binaryws.com/websockets/v3?app_id={app_id}"


async def _authorized_ws(app_id: str, api_token: str):
    """Async context manager: yields an authorized WebSocket."""
    url = _WS_URL.format(app_id=app_id)
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"authorize": api_token}))
        auth = json.loads(await ws.recv())
        if "error" in auth:
            raise ValueError(f"Deriv auth error: {auth['error']['message']}")
        yield ws


async def get_account_info(app_id: str, api_token: str) -> dict:
    async with _authorized_ws(app_id, api_token) as ws:
        await ws.send(json.dumps({"balance": 1}))
        result = json.loads(await ws.recv())
        if "error" in result:
            raise ValueError(result["error"]["message"])
        b = result["balance"]
        return {
            "balance":  b["balance"],
            "currency": b["currency"],
            "equity":   b["balance"],
            "profit":   0,
        }


# Action values accepted from the router / strategy:
#   "rise" | "BUY"  → CALL
#   "fall" | "SELL" → PUT
def _contract_type(action: str) -> str:
    return "CALL" if action.upper() in {"RISE", "BUY", "CALL"} else "PUT"


async def execute_trade(
    app_id: str,
    api_token: str,
    symbol: str,
    action: str,
    amount: float,
    duration: int = 5,
    duration_unit: str = "m",   # "t" ticks | "s" sec | "m" min | "h" hr | "d" day
) -> dict:
    """
    Two-step proposal → buy.
    Raises ValueError on any Deriv API error so the router gets a clean 400/500.
    """
    contract_type = _contract_type(action)

    async with _authorized_ws(app_id, api_token) as ws:

        # ── 1. proposal ───────────────────────────────────────────────
        await ws.send(json.dumps({
            "proposal":      1,
            "amount":        amount,
            "basis":         "stake",
            "contract_type": contract_type,
            "currency":      "USD",
            "duration":      duration,
            "duration_unit": duration_unit,
            "symbol":        symbol,        # "R_75", "R_100", "1HZ75V" …
        }))
        p_resp = json.loads(await ws.recv())
        if "error" in p_resp:
            raise ValueError(f"Proposal failed: {p_resp['error']['message']}")

        proposal    = p_resp["proposal"]
        proposal_id = proposal["id"]
        ask_price   = proposal["ask_price"]

        # ── 2. buy ────────────────────────────────────────────────────
        await ws.send(json.dumps({
            "buy":   proposal_id,
            "price": ask_price,     # guarantees fill at quoted price
        }))
        b_resp = json.loads(await ws.recv())
        if "error" in b_resp:
            raise ValueError(f"Buy failed: {b_resp['error']['message']}")

        c = b_resp["buy"]
        return {
            "contract_id":    c["contract_id"],
            "transaction_id": c["transaction_id"],
            "buy_price":      c["buy_price"],
            "payout":         c["payout"],
            "contract_type":  contract_type,
            "symbol":         symbol,
            "longcode":       c.get("longcode", ""),
        }


async def watch_contract(app_id: str, api_token: str, contract_id: int, callback):
    """
    Subscribes to contract updates and calls callback(data) on each tick
    until the contract is sold/expired.
    """
    url = _WS_URL.format(app_id=app_id)
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"authorize": api_token}))
        auth = json.loads(await ws.recv())
        if "error" in auth:
            raise ValueError(f"Deriv auth error: {auth['error']['message']}")

        await ws.send(json.dumps({
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe": 1,
        }))

        while True:
            msg = json.loads(await ws.recv())
            if "error" in msg:
                break
            contract = msg.get("proposal_open_contract", {})
            if not contract:
                continue

            await callback({
                "contract_id":  contract_id,
                "status":       contract.get("status", "open"),
                "buy_price":    contract.get("buy_price", 0),
                "bid_price":    contract.get("bid_price", 0),
                "profit":       contract.get("profit", 0),
                "profit_pct":   contract.get("profit_percentage", 0),
                "entry_spot":   contract.get("entry_spot", 0),
                "current_spot": contract.get("current_spot", 0),
                "expiry_time":  contract.get("expiry_time", 0),
                "is_expired":   contract.get("is_expired", False),
                "is_sold":      contract.get("is_sold", False),
            })

            if contract.get("is_expired") or contract.get("is_sold"):
                break