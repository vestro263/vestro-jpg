"""
routes/auth.py
==============
Flow: Google OAuth → Deriv OAuth → account selector → dashboard
All non-wallet accounts linked to the user's email are shown in the selector.
"""

import json
import os
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Credentials, User
from ..services.credential_store import encrypt, decrypt
from ..services.deriv_ws import get_account_info, get_mt5_login_list, get_linked_accounts
router = APIRouter()

DERIV_APP_ID          = os.environ["DERIV_APP_ID"]
FRONTEND_URL          = os.environ.get("FRONTEND_URL",         "https://vestro-ui.onrender.com")
BACKEND_URL           = os.environ.get("BACKEND_URL",          "https://vestro-jpg.onrender.com")
GOOGLE_CLIENT_ID      = os.environ.get("GOOGLE_CLIENT_ID",     "")
GOOGLE_CLIENT_SECRET  = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT       = f"{BACKEND_URL}/auth/google/callback"

WALLET_PREFIXES = ("VRW", "RW")


def _is_wallet(account_id: str) -> bool:
    return account_id.startswith(WALLET_PREFIXES)


# ── STEP 1 — Start Google login ───────────────────────────────

@router.get("/auth/google")
async def google_login(user_id: str = ""):
    params = urllib.parse.urlencode({
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  GOOGLE_REDIRECT,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         user_id,
        "access_type":   "online",
        "prompt":        "select_account",
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


# ── STEP 2 — Google callback ──────────────────────────────────

@router.get("/auth/google/callback")
async def google_callback(
    code:  str = "",
    state: str = "",
    error: str = "",
    db: AsyncSession = Depends(get_db),
):
    if error or not code:
        return RedirectResponse(f"{FRONTEND_URL}?error=google_auth_failed")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code":          code,
                "client_id":     GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri":  GOOGLE_REDIRECT,
                "grant_type":    "authorization_code",
            },
        )
        token_data = token_resp.json()
        if "error" in token_data:
            print(f"[google_callback] token error: {token_data}")
            return RedirectResponse(f"{FRONTEND_URL}?error=google_token_failed")

        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        userinfo = userinfo_resp.json()

    email      = userinfo.get("email", "")
    name       = userinfo.get("name",  "")
    avatar_url = userinfo.get("picture", "")

    if not email:
        return RedirectResponse(f"{FRONTEND_URL}?error=no_email")

    result = await db.execute(select(User).where(User.email == email))
    user   = result.scalar_one_or_none()
    if not user:
        user = User(email=email, name=name, avatar_url=avatar_url)
        db.add(user)
    else:
        user.name       = name
        user.avatar_url = avatar_url
    await db.commit()
    await db.refresh(user)

    deriv_url = (
        f"https://oauth.deriv.com/oauth2/authorize"
        f"?app_id={DERIV_APP_ID}&l=EN&brand=deriv"
        f"&state={user.id}"
    )
    return RedirectResponse(deriv_url)


# ── STEP 3 — Deriv OAuth callback ────────────────────────────

@router.get("/auth/deriv/callback")
async def deriv_callback(request: Request, db: AsyncSession = Depends(get_db)):
    params  = dict(request.query_params)
    user_id = params.get("state", "")

    print("==== DERIV CALLBACK ====", params)

    user = None
    if user_id:
        result = await db.execute(select(User).where(User.id == user_id))
        user   = result.scalar_one_or_none()

    accounts = []
    i = 1

    while f"acct{i}" in params:
        acct  = params[f"acct{i}"]
        token = params[f"token{i}"]
        cur   = params.get(f"cur{i}", "USD")

        if _is_wallet(acct):
            print(f"[wallet] {acct} — fetching linked accounts via account_list")
            try:
                linked = await get_linked_accounts(DERIV_APP_ID, token)
                for la in linked:
                    la_acct  = la["account_id"]
                    la_token = la.get("token", token)
                    try:
                        info    = await get_account_info(DERIV_APP_ID, la_token)
                        is_demo = bool(info.get("is_virtual", False))

                        result = await db.execute(
                            select(Credentials).where(Credentials.account_id == la_acct)
                        )
                        cred = result.scalar_one_or_none()
                        if not cred:
                            cred = Credentials()
                            db.add(cred)

                        cred.account_id      = la_acct
                        cred.is_demo         = is_demo
                        cred.broker          = "deriv"
                        cred.login           = encrypt(la_acct)
                        cred.password        = encrypt(la_token)
                        cred.api_token       = encrypt(la_token)
                        cred.server          = encrypt("")
                        cred.meta_account_id = ""
                        if user:
                            cred.google_user_id = user.id

                        await db.flush()

                        accounts.append({
                            "account_id": la_acct,
                            "loginid":    la_acct,
                            "balance":    info.get("balance", 0),
                            "currency":   info.get("currency", "USD"),
                            "name":       info.get("name", ""),
                            "type":       "demo" if is_demo else "real",
                            "is_demo":    is_demo,
                            "broker":     "deriv",
                            "user_id":    user.id    if user else "",
                            "email":      user.email if user else "",
                        })
                    except Exception as e:
                        print(f"[ERROR] get_account_info for linked {la_acct}: {e}")
            except Exception as e:
                print(f"[ERROR] get_linked_accounts for wallet {acct}: {e}")
            i += 1
            continue

        # ── non-wallet account ────────────────────────────────
        try:
            info = await get_account_info(DERIV_APP_ID, token)
            print(f"[info] {acct}:", info)
        except Exception as e:
            print(f"[ERROR] get_account_info {acct}: {e}")
            i += 1
            continue

        result = await db.execute(
            select(Credentials).where(Credentials.account_id == acct)
        )
        cred = result.scalar_one_or_none()
        if not cred:
            cred = Credentials()
            db.add(cred)

        is_demo = bool(info.get("is_virtual", False))

        cred.account_id      = acct
        cred.is_demo         = is_demo
        cred.broker          = "deriv"
        cred.login           = encrypt(acct)
        cred.password        = encrypt(token)
        cred.api_token       = encrypt(token)
        cred.server          = encrypt("")
        cred.meta_account_id = ""
        if user:
            cred.google_user_id = user.id

        await db.flush()

        accounts.append({
            "account_id": acct,
            "loginid":    acct,
            "balance":    info.get("balance", 0),
            "currency":   cur,
            "name":       info.get("name", ""),
            "type":       "demo" if is_demo else "real",
            "is_demo":    is_demo,
            "broker":     "deriv",
            "user_id":    user.id    if user else "",
            "email":      user.email if user else "",
        })

        i += 1

    await db.commit()

    if not accounts:
        uid = user.id if user else ""
        return RedirectResponse(f"{FRONTEND_URL}?error=no_deriv_accounts&user_id={uid}")

    accounts_json = urllib.parse.quote(json.dumps(accounts))
    uid    = user.id             if user else ""
    active = user.active_account if user else ""
    return RedirectResponse(
        f"{FRONTEND_URL}?accounts={accounts_json}&user_id={uid}&active_account={active}"
    )

# ── STEP 4 — Set active account ───────────────────────────────

class SetActiveAccount(BaseModel):
    deriv_account: str
    user_id:       str


@router.post("/auth/set-active-account")
async def set_active_account(body: SetActiveAccount, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == body.user_id))
    user   = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.active_account = body.deriv_account
    await db.commit()
    return {"status": "ok", "active": body.deriv_account}


@router.get("/auth/active-account/{user_id}")
async def get_active_account(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if not user or not user.active_account:
        raise HTTPException(status_code=404, detail="No active account set")

    result = await db.execute(
        select(Credentials).where(Credentials.account_id == user.active_account)
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    return {
        "deriv_account": cred.account_id,
        "api_token":     decrypt(cred.password),
        "is_demo":       cred.is_demo,
    }


# ── STEP 5 — Auth check (session restore) ────────────────────
# Returns ALL accounts linked to this user — not just demo

@router.get("/auth/check/{user_id}")
async def check_auth(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if not user:
        return {"found": False}

    # ← removed is_demo filter — return ALL linked accounts
    result = await db.execute(
        select(Credentials).where(Credentials.google_user_id == user.id)
    )
    creds = result.scalars().all()

    return {
        "found":          True,
        "user_id":        user.id,
        "email":          user.email,
        "name":           user.name,
        "active_account": user.active_account or "",
        "accounts": [
            {
                "account_id": c.account_id,
                "loginid":    c.account_id,
                "type":       "demo" if c.is_demo else "real",
                "is_demo":    c.is_demo,
                "broker":     c.broker or "deriv",
            }
            for c in creds
            if c.account_id
        ],
    }


# ── STEP 6 — Link MT5 account ─────────────────────────────────

class LinkDemoAccount(BaseModel):
    mt5_login_id: str
    user_id:      str


@router.post("/auth/link-demo-account")
async def link_demo_account(body: LinkDemoAccount, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == body.user_id))
    user   = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = await db.execute(
        select(Credentials).where(Credentials.google_user_id == user.id)
    )
    creds = result.scalars().all()
    if not creds:
        raise HTTPException(status_code=404, detail="No Deriv accounts found. Please sign in again.")

    matched_cred = None
    for cred in creds:
        try:
            token        = decrypt(cred.password)
            mt5_accounts = await get_mt5_login_list(DERIV_APP_ID, token)
            if any(str(a.get("login")) == body.mt5_login_id for a in mt5_accounts):
                matched_cred = cred
                break
        except Exception as e:
            print(f"[link-demo-account] error for {cred.account_id}: {e}")
            continue

    if not matched_cred:
        raise HTTPException(status_code=404, detail="MT5 login not found. Double-check the Login ID.")

    matched_cred.google_user_id = user.id
    if not user.active_account:
        user.active_account = matched_cred.account_id

    await db.commit()
    return {"status": "ok", "account_id": matched_cred.account_id, "active": user.active_account}