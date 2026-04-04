from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Credentials
from ..services.credential_store import encrypt
from ..services.deriv_ws import get_account_info
import os

router = APIRouter()

DERIV_APP_ID = os.environ["DERIV_APP_ID"]
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://vestro-ui.onrender.com")

@router.get("/auth/deriv/callback")
async def deriv_callback(token1: str, acct1: str, cur1: str = "USD", db: AsyncSession = Depends(get_db)):
    try:
        info = await get_account_info(DERIV_APP_ID, token1)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    account_id = acct1

    result = await db.execute(select(Credentials).where(Credentials.user_id == account_id))
    cred = result.scalar_one_or_none()
    if not cred:
        cred = Credentials(user_id=account_id)
        db.add(cred)
    cred.broker          = "deriv"
    cred.login           = encrypt(account_id)
    cred.password        = encrypt(token1)
    cred.server          = encrypt("")
    cred.meta_account_id = ""
    await db.commit()

    return RedirectResponse(
        f"{FRONTEND_URL}"
        f"?account_id={account_id}"
        f"&balance={info.get('balance', 0)}"
        f"&currency={info.get('currency', cur1)}"
    )

@router.get("/auth/check/{account_id}")
async def check_account(account_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Credentials).where(Credentials.user_id == account_id))
    cred = result.scalar_one_or_none()
    if not cred:
        return {"found": False}
    return {
        "found": True,
        "broker": cred.broker,
        "user_id": cred.user_id,
    }