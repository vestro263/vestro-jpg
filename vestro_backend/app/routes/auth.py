"""
routes/auth.py
==============
Identity model:
  Credentials.account_id     = Deriv loginid e.g. VRTC9999            (clean, queryable)
  Credentials.google_user_id = internal User.id FK                     (login identity)
  Credentials.is_demo        = derived once on save, trusted forever   (never recomputed)

Demo-only policy:
  Vestro only operates on Deriv VRTC demo accounts.
  Wallet accounts (VRW, RW) are silently skipped.
  Real accounts (CR, MF, etc.) are rejected at the OAuth callback.
  If no VRTC account is found, the user is redirected to create one on Deriv.
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
from ..services.deriv_ws import get_account_info

router = APIRouter()

DERIV_APP_ID         = os.environ["DERIV_APP_ID"]
FRONTEND_URL         = os.environ.get("FRONTEND_URL",         "https://vestro-ui.onrender.com")
BACKEND_URL          = os.environ.get("BACKEND_URL",          "https://vestro-jpg.onrender.com")
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID",     "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT      = f"{BACKEND_URL}/auth/google/callback"

DEMO_PREFIX          = "VRTC"
WALLET_PREFIXES      = ("VRW", "RW")
DERIV_CREATE_DEMO_URL = "https://hub.deriv.com/tradershub/home"


def _is_wallet(account_id: str) -> bool:
    return account_id.startswith(WALLET_PREFIXES)


def _is_demo(account_id: str) -> bool:
    return account_id.startswith(DEMO_PREFIX)


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

    # Upsert User by email
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

    # Always go through Deriv OAuth to get fresh tokens.
    # This ensures newly created demo accounts are always picked up
    # and tokens are never stale.
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

    print(f"[deriv_callback] params: {params}")

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
            print(f"[deriv_callback] skipping wallet account {acct}")
            i += 1
            continue

        if not _is_demo(acct):
            print(f"[deriv_callback] skipping real account {acct} — demo-only mode")
            i += 1
            continue

        # Only VRTC reaches here
        try:
            info = await get_account_info(DERIV_APP_ID, token)
        except Exception as e:
            print(f"[deriv_callback] get_account_info failed for {acct}: {e}")
            i += 1
            continue

        result = await db.execute(
            select(Credentials).where(Credentials.account_id == acct)
        )
        cred = result.scalar_one_or_none()
        if not cred:
            cred = Credentials()
            db.add(cred)

        cred.account_id      = acct
        cred.is_demo         = bool(info.get("is_virtual", False))
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
            "balance":    info.get("balance", 0),
            "currency":   cur,
            "name":       info.get("name", ""),
            "type":       "demo",
            "is_demo":    True,
            "broker":     "deriv",
            "user_id":    user.id    if user else "",
            "email":      user.email if user else "",
        })
        i += 1

    await db.commit()

    if not accounts:
        return RedirectResponse(
            f"{FRONTEND_URL}?error=demo_account_required"
            f"&deriv_demo_url={DERIV_CREATE_DEMO_URL}"
        )

    accounts_json = urllib.parse.quote(json.dumps(accounts))
    uid    = user.id             if user else ""
    active = user.active_account if user else ""
    return RedirectResponse(
        f"{FRONTEND_URL}?accounts={accounts_json}&user_id={uid}&active_account={active}"
    )


# ── STEP 4 — Set active account (DB-persisted) ───────────────

class SetActiveAccount(BaseModel):
    deriv_account: str
    user_id:       str


@router.post("/auth/set-active-account")
async def set_active_account(body: SetActiveAccount, db: AsyncSession = Depends(get_db)):
    if not _is_demo(body.deriv_account):
        raise HTTPException(
            status_code=400,
            detail=f"Only demo accounts can be set as active. Got: {body.deriv_account}",
        )

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


# ── STEP 5 — Auth check on app load ──────────────────────────

@router.get("/auth/check/{user_id}")
async def check_auth(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if not user:
        return {"found": False}

    result = await db.execute(
        select(Credentials).where(
            Credentials.google_user_id == user.id,
            Credentials.is_demo == True,              # noqa: E712
        )
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
                "type":       "demo",
                "is_demo":    True,
                "broker":     c.broker,
            }
            for c in creds
            if c.account_id
        ],
    }