# vestro_backend/app/routes/mt5.py
from fastapi import APIRouter, Header
from ..services.deriv_ws import get_account_info, place_trade
import asyncio

router = APIRouter()

@router.get("/api/account")
async def account(x_api_token: str = Header(...)):
    return await get_account_info(x_api_token)

@router.post("/api/trade")
async def trade(body: dict, x_api_token: str = Header(...)):
    return await place_trade(x_api_token, body["symbol"], body["action"], body["amount"])