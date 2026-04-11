from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends
from sqlalchemy import select
from ..database import get_db
from ..models import Credentials
from ..services.credential_store import decrypt
from ..services.deriv_ws import get_account_info, place_trade

router = APIRouter()

async def get_token_for_account(account_id: str, db: AsyncSession) -> str:
    result = await db.execute(
        select(Credentials).where(Credentials.user_id == account_id)
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail=f"No credentials for {account_id}")
    return decrypt(cred.password)

@router.get("/api/account/{account_id}")
async def account(account_id: str, db: AsyncSession = Depends(get_db)):
    token = await get_token_for_account(account_id, db)
    info  = await get_account_info(token)
    return {
        **info,
        "account_id": account_id,
        "is_virtual": account_id.startswith("VRT"),
    }

@router.post("/api/trade")
async def trade(body: dict, db: AsyncSession = Depends(get_db)):
    account_id = body.get("account_id")
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id is required")
    token = await get_token_for_account(account_id, db)
    return await place_trade(token, body["symbol"], body["action"], body["amount"])