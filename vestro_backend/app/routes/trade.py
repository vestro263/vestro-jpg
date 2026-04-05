from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Credentials
from ..services.credential_store import decrypt
from ..services import welltrade
from ..services.deriv_ws import get_account_info, execute_trade as deriv_trade, watch_contract
from pydantic import BaseModel
import os
import asyncio
import httpx

router = APIRouter()
DERIV_APP_ID = os.environ["DERIV_APP_ID"]
BACKEND_URL  = os.environ.get("BACKEND_URL", "https://vestro-jpg.onrender.com")

class TradeBody(BaseModel):
    broker: str
    symbol: str
    action: str
    volume: float = 0.01
    amount: float = 1.0
    sl: float = 0
    tp: float = 0
    account_id: str = ""

@router.get("/api/account/{user_id}")
async def get_account(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Credentials).where(Credentials.user_id == user_id))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="No credentials found")
    if cred.broker == "welltrade":
        return await welltrade.get_account_info(cred.meta_account_id)
    else:
        return await get_account_info(DERIV_APP_ID, decrypt(cred.password))

from fastapi import HTTPException

MIN_STAKE = 0.35  # 🔥 define once

@router.post("/api/trade")
async def trade(body: TradeBody, db: AsyncSession = Depends(get_db)):
    if body.account_id:
        result = await db.execute(
            select(Credentials).where(Credentials.user_id == body.account_id)
        )
    else:
        result = await db.execute(
            select(Credentials)
            .where(Credentials.broker == body.broker)
            .order_by(Credentials.id.desc())
            .limit(1)
        )

    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Broker not connected")

    # =========================
    # ✅ WELLTRADE (unchanged)
    # =========================
    if body.broker == "welltrade":
        return await welltrade.execute_trade(
            cred.meta_account_id,
            body.symbol,
            body.action,
            body.volume,
            body.sl,
            body.tp
        )

    # =========================
    # ✅ DERIV FIX STARTS HERE
    # =========================
    else:
        # 🔥 VALIDATE stake BEFORE hitting Deriv
        amount = float(body.amount or 0)

        if amount < MIN_STAKE:
            raise HTTPException(
                status_code=400,
                detail=f"Minimum stake is {MIN_STAKE}"
            )

        api_token = decrypt(cred.password)

        trade_result = await deriv_trade(
            DERIV_APP_ID,
            api_token,
            body.symbol,
            body.action,
            amount
        )

        # 🔥 Handle safe error (no crash)
        if trade_result.get("status") == "error":
            raise HTTPException(
                status_code=400,
                detail=trade_result.get("message")
            )

        contract_id = trade_result.get("contract_id")

        if contract_id:
            asyncio.create_task(
                _watch_and_broadcast(
                    contract_id,
                    api_token,
                    body.symbol,
                    trade_result
                )
            )

        return trade_result

async def _watch_and_broadcast(contract_id: int, api_token: str, symbol: str, trade_result: dict):
    async with httpx.AsyncClient() as client:
        async def on_update(data):
            try:
                await client.post(
                    f"{BACKEND_URL}/api/contract/update",
                    json={**data, "symbol": symbol, "contract_type": trade_result.get("contract_type")},
                    timeout=5,
                )
            except Exception as e:
                print(f"[trade] contract broadcast error: {e}")

        try:
            await watch_contract(DERIV_APP_ID, api_token, contract_id, on_update)
        except Exception as e:
            print(f"[trade] watch_contract error: {e}")