from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Credentials
from ..services.credential_store import decrypt
from ..services.deriv_ws import get_account_info, execute_trade as place_trade
import os

router = APIRouter()
DERIV_APP_ID = os.environ["DERIV_APP_ID"]


async def get_cred_for_account(account_id: str, db: AsyncSession) -> Credentials:
    """
    Fetch a Credentials row by account_id (clean Deriv loginid).
    Raises 404 if not found.
    Single source — replaces the old get_token_for_account which discarded
    the cred and left is_demo unreachable at the call site.
    """
    result = await db.execute(
        select(Credentials).where(Credentials.account_id == account_id)
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail=f"No credentials for {account_id}")
    return cred


@router.get("/api/account/{account_id}")
async def account(account_id: str, db: AsyncSession = Depends(get_db)):
    cred  = await get_cred_for_account(account_id, db)
    token = decrypt(cred.password)
    info  = await get_account_info(DERIV_APP_ID, token)
    return {
        **info,
        "account_id": cred.account_id,
        "is_virtual": cred.is_demo,   # from DB — never derived from prefix at runtime
    }


@router.post("/api/trade")
async def trade(body: dict, db: AsyncSession = Depends(get_db)):
    account_id = body.get("account_id")
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id is required")
    cred = await get_cred_for_account(account_id, db)
    return await place_trade(
        DERIV_APP_ID,
        decrypt(cred.password),
        body["symbol"],
        body["action"],
        body["amount"],
    )