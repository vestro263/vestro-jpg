from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Credentials
from ..services.credential_store import decrypt
from ..services import welltrade
from ..services.deriv_ws import get_account_info, execute_trade as deriv_trade
from pydantic import BaseModel
import os

router = APIRouter()
DERIV_APP_ID = os.environ["DERIV_APP_ID"]

class TradeBody(BaseModel):
    broker: str
    symbol: str
    action: str
    volume: float = 0.01
    amount: float = 1.0
    sl: float = 0
    tp: float = 0

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

@router.post("/api/trade")
async def trade(body: TradeBody, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Credentials).where(
            Credentials.broker == body.broker
        )
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Broker not connected")
    if body.broker == "welltrade":
        return await welltrade.execute_trade(
            cred.meta_account_id, body.symbol,
            body.action, body.volume, body.sl, body.tp
        )
    else:
        return await deriv_trade(
            DERIV_APP_ID, decrypt(cred.password),
            body.symbol, body.action, body.amount
        )