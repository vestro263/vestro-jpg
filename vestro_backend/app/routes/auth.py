from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Credentials
from ..services.credential_store import encrypt
from ..services.deriv_ws import get_account_info
import os, json

router = APIRouter()

DERIV_APP_ID = os.environ["DERIV_APP_ID"]
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://vestro-ui.onrender.com")

@router.get("/auth/deriv/callback")
async def deriv_callback(request_params: dict = None, db: AsyncSession = Depends(get_db), **kwargs):
    from fastapi import Request
    pass

# Replace the whole route with this:
from fastapi import Request

@router.get("/auth/deriv/callback")
async def deriv_callback(request: Request, db: AsyncSession = Depends(get_db)):
    params = dict(request.query_params)

    # Collect all accounts Deriv sent
    accounts = []
    i = 1
    while f"acct{i}" in params:
        acct   = params[f"acct{i}"]
        token  = params[f"token{i}"]
        cur    = params.get(f"cur{i}", "USD")

        try:
            info = await get_account_info(DERIV_APP_ID, token)
        except Exception:
            i += 1
            continue

        # Save each account
        result = await db.execute(select(Credentials).where(Credentials.user_id == acct))
        cred = result.scalar_one_or_none()
        if not cred:
            cred = Credentials(user_id=acct)
            db.add(cred)
        cred.broker          = "deriv"
        cred.login           = encrypt(acct)
        cred.password        = encrypt(token)
        cred.server          = encrypt("")
        cred.meta_account_id = ""
        await db.flush()

        accounts.append({
            "account_id": acct,
            "balance":    info.get("balance", 0),
            "currency":   cur,
            "type":       "demo" if acct.startswith("VRT") else "real",
        })
        i += 1

    await db.commit()

    # Pass all accounts to frontend as JSON in query param
    import urllib.parse
    accounts_json = urllib.parse.quote(json.dumps(accounts))
    return RedirectResponse(f"{FRONTEND_URL}?accounts={accounts_json}")


@router.get("/auth/check/{account_id}")
async def check_account(account_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Credentials).where(Credentials.user_id == account_id))
    cred = result.scalar_one_or_none()
    if not cred:
        return {"found": False}
    return {"found": True, "broker": cred.broker, "user_id": cred.user_id}