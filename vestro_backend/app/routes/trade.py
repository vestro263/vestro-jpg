# vestro_backend/app/routes/trade.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Credentials
from ..services.credential_store import encrypt, decrypt
from ..services import welltrade, deriv
from pydantic import BaseModel
import os

router = APIRouter()
DERIV_APP_ID = os.environ["DERIV_APP_ID"]

class ConnectBody(BaseModel):
    broker: str          # "deriv" or "welltrade"
    login: str           # WelTrade: MT5 login | Deriv: account loginid
    password: str        # WelTrade: MT5 password | Deriv: api_token
    server: str = ""     # WelTrade only
    meta_account_id: str = ""  # MetaApi account ID (WelTrade)

class TradeBody(BaseModel):
    broker: str
    symbol: str
    action: str          # "BUY" or "SELL"
    volume: float = 0.01
    amount: float = 1.0  # Deriv stake
    sl: float = 0
    tp: float = 0

@router.post("/api/connect")
async def connect(body: ConnectBody, db: Session = Depends(get_db)):
    # Verify credentials work before storing
    try:
        if body.broker == "welltrade":
            info = await welltrade.get_account_info(body.meta_account_id)
        else:
            info = await deriv.get_account_info(DERIV_APP_ID, body.password)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Store encrypted
    cred = db.query(Credentials).filter_by(user_id=body.login).first()
    if not cred:
        cred = Credentials(user_id=body.login)
        db.add(cred)
    cred.broker = body.broker
    cred.login = encrypt(body.login)
    cred.password = encrypt(body.password)
    cred.server = encrypt(body.server)
    cred.meta_account_id = body.meta_account_id
    db.commit()

    return {"ok": True, "account": info}

@router.get("/api/account/{user_id}")
async def get_account(user_id: str, db: Session = Depends(get_db)):
    cred = db.query(Credentials).filter_by(user_id=user_id).first()
    if not cred:
        raise HTTPException(status_code=404, detail="No credentials found")
    if cred.broker == "welltrade":
        return await welltrade.get_account_info(cred.meta_account_id)
    else:
        return await deriv.get_account_info(DERIV_APP_ID, decrypt(cred.password))

@router.post("/api/trade")
async def trade(body: TradeBody, db: Session = Depends(get_db)):
    cred = db.query(Credentials).filter_by(broker=body.broker).first()
    if not cred:
        raise HTTPException(status_code=404, detail="Broker not connected")
    if body.broker == "welltrade":
        return await welltrade.execute_trade(
            cred.meta_account_id, body.symbol,
            body.action, body.volume, body.sl, body.tp
        )
    else:
        return await deriv.execute_trade(
            DERIV_APP_ID, decrypt(cred.password),
            body.symbol, body.action, body.amount
        )