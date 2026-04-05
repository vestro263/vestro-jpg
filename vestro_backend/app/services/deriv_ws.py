import json
import websockets

_WS_URL = "wss://ws.binaryws.com/websockets/v3?app_id={app_id}"


from contextlib import asynccontextmanager

@asynccontextmanager
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
    url = _WS_URL.format(app_id=app_id)
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"authorize": api_token}))
        auth = json.loads(await ws.recv())
        if "error" in p_resp:
            return {
                "status": "error",
                "message": p_resp["error"]["message"]
            }
        a = auth["authorize"]

        await ws.send(json.dumps({"balance": 1}))
        bal_resp = json.loads(await ws.recv())
        balance = bal_resp["balance"]["balance"] if "balance" in bal_resp else a.get("balance", 0)

        return {
            "account_id":  a.get("loginid", ""),
            "balance":     balance,
            "currency":    a.get("currency", "USD"),
            "equity":      balance,
            "profit":      0,
            "margin_free": balance,
            "name":        a.get("fullname", ""),
            "email":       a.get("email", ""),
            "is_virtual":  a.get("is_virtual", 0) == 1,
            "leverage":    0,
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
    duration_unit: str = "m",
) -> dict:
    contract_type = _contract_type(action)
    url = _WS_URL.format(app_id=app_id)

    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"authorize": api_token}))
        auth = json.loads(await ws.recv())
        if "error" in auth:
            return {
                "status": "error",
                "message": auth["error"]["message"]
            }

        await ws.send(json.dumps({
            "proposal":      1,
            "amount":        amount,
            "basis":         "stake",
            "contract_type": contract_type,
            "currency":      "USD",
            "duration":      duration,
            "duration_unit": duration_unit,
            "symbol":        symbol,
        }))
        p_resp = json.loads(await ws.recv())
        if "error" in p_resp:
            return {
                "status": "error",
                "message": p_resp["error"]["message"]
            }

        proposal_id = p_resp["proposal"]["id"]
        ask_price   = p_resp["proposal"]["ask_price"]

        await ws.send(json.dumps({"buy": proposal_id, "price": ask_price}))
        b_resp = json.loads(await ws.recv())
        if "error" in b_resp:
            return {
                "status": "error",
                "message": b_resp["error"]["message"]
            }

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
    url = _WS_URL.format(app_id=app_id)
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"authorize": api_token}))
        auth = json.loads(await ws.recv())
        if "error" in auth:
            return {
                "status": "error",
                "message": f"Auth error: {auth['error']['message']}"
            }

        await ws.send(json.dumps({
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe": 1,
        }))

        while True:
            msg      = json.loads(await ws.recv())
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