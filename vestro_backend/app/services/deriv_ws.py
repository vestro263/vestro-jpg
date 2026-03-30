# vestro_backend/app/services/deriv.py
import asyncio, json, websockets

WS_URL = "wss://ws.binaryws.com/websockets/v3?app_id={app_id}"

async def _call(app_id: str, api_token: str, payload: dict) -> dict:
    url = WS_URL.format(app_id=app_id)
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"authorize": api_token}))
        auth = json.loads(await ws.recv())
        if "error" in auth:
            raise ValueError(auth["error"]["message"])
        await ws.send(json.dumps(payload))
        return json.loads(await ws.recv())

async def get_account_info(app_id: str, api_token: str) -> dict:
    result = await _call(app_id, api_token, {"balance": 1})
    b = result["balance"]
    return {
        "balance": b["balance"],
        "currency": b["currency"],
        "equity": b["balance"],
        "profit": 0,
    }

async def execute_trade(app_id: str, api_token: str,
                        symbol: str, action: str, amount: float) -> dict:
    contract_type = "CALL" if action == "BUY" else "PUT"
    payload = {
        "buy": 1,
        "price": amount,
        "parameters": {
            "amount": amount,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": 5,
            "duration_unit": "m",
            "symbol": symbol,   # e.g. "R_100" for Volatility 100
        }
    }
    result = await _call(app_id, api_token, payload)
    return result