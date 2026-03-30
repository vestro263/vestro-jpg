# vestro_backend/app/services/ctrader.py
import httpx

CTRADER_BASE = "https://api.ctrader.com/v2"

async def get_account(access_token: str, account_id: str):
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{CTRADER_BASE}/accounts/{account_id}",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        return r.json()

async def place_order(access_token: str, account_id: str, symbol: str, side: str, volume: float):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{CTRADER_BASE}/orders",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "accountId": account_id,
                "symbolName": symbol,
                "orderType": "MARKET",
                "tradeSide": side,  # "BUY" or "SELL"
                "volume": int(volume * 100),  # in lots * 100
            }
        )
        return r.json()