# vestro_backend/app/services/welltrade.py
from metaapi_cloud_sdk import MetaApi
import os, asyncio

META_TOKEN = os.environ["METAAPI_TOKEN"]  # from metaapi.cloud dashboard

async def _get_connection(account_id: str):
    api = MetaApi(META_TOKEN)
    account = await api.metatrader_account_api.get_account(account_id)
    if account.state not in ("DEPLOYING", "DEPLOYED"):
        await account.deploy()
    await account.wait_deployed()
    conn = account.get_rpc_connection()
    await conn.connect()
    await conn.wait_synchronized()
    return conn

async def get_account_info(account_id: str) -> dict:
    conn = await _get_connection(account_id)
    info = await conn.get_account_information()
    positions = await conn.get_positions()
    return {"account": info, "positions": positions}

async def execute_trade(account_id: str, symbol: str, action: str,
                        volume: float, sl: float = 0, tp: float = 0) -> dict:
    conn = await _get_connection(account_id)
    if action == "BUY":
        result = await conn.create_market_buy_order(
            symbol, volume,
            stop_loss=sl or None,
            take_profit=tp or None,
        )
    else:
        result = await conn.create_market_sell_order(
            symbol, volume,
            stop_loss=sl or None,
            take_profit=tp or None,
        )
    return result