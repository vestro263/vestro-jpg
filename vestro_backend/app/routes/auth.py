"""
routes/auth.py
==============
Uses ONLY existing DB columns + one new nullable column:
  Credentials.google_user_id VARCHAR (added via init_db migration)
  Credentials.user_id        = Deriv loginid e.g. CR123456 (unchanged)
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

_active_accounts: dict[str, str] = {}   # google user.id → Deriv loginid


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

    # Find Credentials linked to this Google user
    # FIX: query by google_user_id, not user_id (which holds Deriv loginid)
    result = await db.execute(
        select(Credentials).where(Credentials.google_user_id == user.id)
    )
    creds = result.scalars().all()

    if not creds:
        deriv_url = (
            f"https://oauth.deriv.com/oauth2/authorize"
            f"?app_id={DERIV_APP_ID}&l=EN&brand=deriv"
            f"&state={user.id}"
        )
        return RedirectResponse(deriv_url)

    accounts = []
    for cred in creds:
        try:
            info = await get_account_info(DERIV_APP_ID, decrypt(cred.password))
            accounts.append({
                "account_id": cred.user_id,   # FIX: user_id = Deriv loginid
                "balance":    info.get("balance", 0),
                "currency":   info.get("currency", "USD"),
                "name":       info.get("name", ""),
                "type":       "demo" if cred.user_id.startswith("VRT") else "real",
                "broker":     "deriv",
                "user_id":    user.id,
                "email":      user.email,
            })
        except Exception as e:
            print(f"[auth] failed to fetch account {cred.user_id}: {e}")

    if not accounts:
        deriv_url = (
            f"https://oauth.deriv.com/oauth2/authorize"
            f"?app_id={DERIV_APP_ID}&l=EN&brand=deriv"
            f"&state={user.id}"
        )
        return RedirectResponse(deriv_url)

    accounts_json = urllib.parse.quote(json.dumps(accounts))
    return RedirectResponse(f"{FRONTEND_URL}?accounts={accounts_json}&user_id={user.id}")


# ── STEP 3 — Deriv OAuth callback ────────────────────────────

@router.get("/auth/deriv/callback")
async def deriv_callback(request: Request, db: AsyncSession = Depends(get_db)):
    params  = dict(request.query_params)
    user_id = params.get("state", "")   # Google User.id

    user = None
    if user_id:
        result = await db.execute(select(User).where(User.id == user_id))
        user   = result.scalar_one_or_none()

    accounts = []
    i = 1
    while f"acct{i}" in params:
        acct  = params[f"acct{i}"]    # Deriv loginid e.g. CR123456
        token = params[f"token{i}"]
        cur   = params.get(f"cur{i}", "USD")

        try:
            info = await get_account_info(DERIV_APP_ID, token)
        except Exception as e:
            print(f"[auth] get_account_info failed for {acct}: {e}")
            i += 1
            continue

        # FIX: upsert by user_id (Deriv loginid), set google_user_id as the link
        result = await db.execute(
            select(Credentials).where(Credentials.user_id == acct)
        )
        cred = result.scalar_one_or_none()
        if not cred:
            cred = Credentials(user_id=acct)
            db.add(cred)

        cred.broker          = "deriv"
        cred.login           = encrypt(acct)
        cred.password        = encrypt(token)
        cred.api_token       = encrypt(token)
        cred.server          = encrypt("")
        cred.meta_account_id = ""
        if user:
            cred.google_user_id = user.id   # FIX: link via google_user_id
        await db.flush()

        accounts.append({
            "account_id": acct,
            "balance":    info.get("balance", 0),
            "currency":   cur,
            "name":       info.get("name", ""),
            "type":       "demo" if acct.startswith("VRT") else "real",
            "broker":     "deriv",
            "user_id":    user.id    if user else "",
            "email":      user.email if user else "",
        })
        i += 1

    await db.commit()

    if not accounts:
        return RedirectResponse(f"{FRONTEND_URL}?error=no_deriv_accounts")

    accounts_json = urllib.parse.quote(json.dumps(accounts))
    uid = user.id if user else ""
    return RedirectResponse(f"{FRONTEND_URL}?accounts={accounts_json}&user_id={uid}")


# ── STEP 4 — Set active account ──────────────────────────────

class SetActiveAccount(BaseModel):
    deriv_account: str   # Deriv loginid e.g. CR123456
    user_id:       str   # Google User.id


@router.post("/auth/set-active-account")
async def set_active_account(body: SetActiveAccount):
    _active_accounts[body.user_id] = body.deriv_account
    return {"status": "ok", "active": body.deriv_account}


@router.get("/auth/active-account/{user_id}")
async def get_active_account(user_id: str, db: AsyncSession = Depends(get_db)):
    deriv_loginid = _active_accounts.get(user_id)
    if not deriv_loginid:
        raise HTTPException(status_code=404, detail="No active account set")

    # FIX: look up by user_id (Deriv loginid), not deriv_account
    result = await db.execute(
        select(Credentials).where(Credentials.user_id == deriv_loginid)
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    return {"deriv_account": deriv_loginid, "api_token": decrypt(cred.password)}


# ── STEP 5 — Auth check on app load ──────────────────────────

@router.get("/auth/check/{user_id}")
async def check_auth(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if not user:
        return {"found": False}

    # FIX: query by google_user_id
    result = await db.execute(
        select(Credentials).where(Credentials.google_user_id == user.id)
    )
    creds = result.scalars().all()

    return {
        "found":    True,
        "user_id":  user.id,
        "email":    user.email,
        "name":     user.name,
        "accounts": [
            {
                "account_id": c.user_id,   # FIX: user_id = Deriv loginid
                "type":       "demo" if (c.user_id or "").startswith("VRT") else "real",
                "broker":     c.broker,
            }
            for c in creds
        ],
    }