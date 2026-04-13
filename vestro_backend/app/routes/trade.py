from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Credentials
from ..services.credential_store import decrypt
from ..services import welltrade
from ..services.deriv_ws import get_account_info, execute_trade as deriv_trade, watch_contract
from pydantic import BaseModel
import os
import asyncio
import httpx
import logging

log = logging.getLogger(__name__)

router = APIRouter()
DERIV_APP_ID = os.environ["DERIV_APP_ID"]
BACKEND_URL  = os.environ.get("BACKEND_URL", "https://vestro-jpg.onrender.com")

MIN_STAKE = 0.35


class TradeBody(BaseModel):
    broker:        str
    symbol:        str
    action:        str
    volume:        float = 0.01
    amount:        float = 1.0
    sl:            float = 0
    tp:            float = 0
    account_id:    str   = ""
    signal_id:     str   = ""   # ← NEW: passed by signal_engine so we can close the log


@router.get("/api/account/{user_id}")
async def get_account(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Credentials).where(Credentials.account_id == account_id))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="No credentials found")
    if cred.broker == "welltrade":
        return await welltrade.get_account_info(cred.meta_account_id)
    else:
        return await get_account_info(DERIV_APP_ID, decrypt(cred.password))


@router.post("/api/trade")
async def trade(body: TradeBody, db: AsyncSession = Depends(get_db)):
    if body.account_id:
        result = await db.execute(
        select(Credentials).where(Credentials.account_id == body.account_id)

        )
    else:
        result = await db.execute(
            select(Credentials)
            .where(Credentials.broker == body.broker)
            .order_by(Credentials.id.desc())
            .limit(1)
        )

    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Broker not connected")

    # ── Welltrade (unchanged) ─────────────────────────────────────────────────
    if body.broker == "welltrade":
        return await welltrade.execute_trade(
            cred.meta_account_id,
            body.symbol,
            body.action,
            body.volume,
            body.sl,
            body.tp,
        )

    # ── Deriv ─────────────────────────────────────────────────────────────────
    amount = float(body.amount or 0)
    if amount < MIN_STAKE:
        raise HTTPException(status_code=400, detail=f"Minimum stake is {MIN_STAKE}")

    api_token   = decrypt(cred.password)
    trade_result = await deriv_trade(
        DERIV_APP_ID, api_token, body.symbol, body.action, amount
    )

    if trade_result.get("status") == "error":
        raise HTTPException(status_code=400, detail=trade_result.get("message"))

    contract_id = trade_result.get("contract_id")
    if contract_id:
        asyncio.create_task(
            _watch_and_broadcast(
                contract_id  = contract_id,
                api_token    = api_token,
                symbol       = body.symbol,
                trade_result = trade_result,
                signal_id    = body.signal_id or None,   # ← forward signal_id
            )
        )

    return trade_result


# ── Contract watcher ──────────────────────────────────────────────────────────
async def _watch_and_broadcast(
    contract_id:  int,
    api_token:    str,
    symbol:       str,
    trade_result: dict,
    signal_id:    str | None = None,
):
    """
    Watch a Deriv contract until it settles.
    - Broadcasts every update to /api/contract/update  (frontend WebSocket feed)
    - On final settlement, posts to /signal/outcome    (closes the signal_log row)
    """
    async with httpx.AsyncClient() as client:

        async def on_update(data: dict):
            # ── 1. broadcast to frontend ──────────────────────────────────
            try:
                await client.post(
                    f"{BACKEND_URL}/api/contract/update",
                    json={
                        **data,
                        "symbol":        symbol,
                        "contract_type": trade_result.get("contract_type"),
                    },
                    timeout=5,
                )
            except Exception as e:
                log.warning("[trade] contract broadcast error: %s", e)

            # ── 2. on settlement, close the signal_log row ────────────────
            is_settled = data.get("is_expired") or data.get("is_sold")
            if is_settled and signal_id:
                exit_price = data.get("exit_spot") or data.get("sell_spot") or 0
                profit     = float(data.get("profit") or 0)

                # Deriv: profit > 0 = WIN, profit < 0 = LOSS, == 0 = NEUTRAL
                if profit > 0:
                    outcome = "WIN"
                elif profit < 0:
                    outcome = "LOSS"
                else:
                    outcome = "NEUTRAL"

                try:
                    resp = await client.post(
                        f"{BACKEND_URL}/signal/outcome",
                        json={
                            "signal_id":  signal_id,
                            "exit_price": float(exit_price),
                            "outcome":    outcome,
                        },
                        timeout=5,
                    )
                    log.info(
                        "[trade] signal %s closed → %s (profit %.2f) status=%s",
                        signal_id, outcome, profit, resp.status_code,
                    )
                except Exception as e:
                    log.warning("[trade] signal outcome write error: %s", e)

        try:
            await watch_contract(DERIV_APP_ID, api_token, contract_id, on_update)
        except Exception as e:
            log.warning("[trade] watch_contract error: %s", e)