# vestro_backend/app/routes/auth.py
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Credentials
from ..services.credential_store import encrypt
from ..services.deriv import get_account_info
import os

router = APIRouter()

DERIV_APP_ID   = os.environ["DERIV_APP_ID"]
FRONTEND_URL   = os.environ.get("FRONTEND_URL", "https://vestro-ui.onrender.com")

@router.get("/auth/deriv/callback")
async def deriv_callback(token1: str, db: Session = Depends(get_db)):
    """
    Deriv redirects here after OAuth login.
    URL looks like: /auth/deriv/callback?token1=a1-xxx&acct1=CR123&cur1=USD
    We grab token1 and use it to fetch account info.
    """
    if not token1:
        raise HTTPException(status_code=400, detail="No token received from Deriv")

    try:
        info = await get_account_info(DERIV_APP_ID, token1)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    account_id = info.get("account_id", "deriv_user")

    # Store encrypted — same pattern as /api/connect
    cred = db.query(Credentials).filter_by(user_id=account_id).first()
    if not cred:
        cred = Credentials(user_id=account_id)
        db.add(cred)
    cred.broker   = "deriv"
    cred.login    = encrypt(account_id)
    cred.password = encrypt(token1)
    cred.server   = encrypt("")
    cred.meta_account_id = ""
    db.commit()

    # Redirect back to frontend with account info in query params
    return RedirectResponse(
        f"{FRONTEND_URL}"
        f"?account_id={account_id}"
        f"&balance={info.get('balance', 0)}"
        f"&currency={info.get('currency', 'USD')}"
    )