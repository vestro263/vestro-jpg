from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.db import get_db
from app.models import Firm, Signal, Score
import json

router = APIRouter(prefix="/api")


@router.get("/health")
async def health():
    return {"status": "ok", "service": "vestro-backend"}


# ── Firms ──────────────────────────────────────────────────────────────────

@router.get("/firms")
async def list_firms(
    limit:          int      = Query(50, le=200),
    sector:         str | None = None,
    min_conviction: int      = Query(0, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(Firm, Score)
        .outerjoin(Score, Score.firm_id == Firm.id)
        .order_by(desc(Score.conviction))
        .limit(limit)
    )
    if sector:
        q = q.where(Firm.sector == sector)
    if min_conviction:
        q = q.where(Score.conviction >= min_conviction)

    rows = (await db.execute(q)).all()
    return [_firm_row(firm, score) for firm, score in rows]


@router.get("/firms/{firm_id}")
async def get_firm(firm_id: str, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(Firm, Score)
        .outerjoin(Score, Score.firm_id == Firm.id)
        .where(Firm.id == firm_id)
    )).first()
    if not row:
        raise HTTPException(status_code=404, detail="Firm not found")
    firm, score = row
    shap = json.loads(score.shap_json) if score and score.shap_json else {}
    return {
        **_firm_row(firm, score),
        "crunchbase_url": firm.crunchbase_url,
        "last_funding_date": firm.last_funding_date.isoformat() if firm.last_funding_date else None,
        "shap": shap,
    }


def _firm_row(firm: Firm, score: Score | None) -> dict:
    return {
        "id":                firm.id,
        "name":              firm.name,
        "domain":            firm.domain,
        "sector":            firm.sector,
        "country":           firm.country,
        "stage":             firm.stage,
        "employee_count":    firm.employee_count,
        "total_funding_usd": firm.total_funding_usd,
        "score": {
            "rise_prob":   score.rise_prob,
            "fall_prob":   score.fall_prob,
            "conviction":  score.conviction,
            "top_driver":  score.top_driver,
            "scored_at":   score.scored_at.isoformat() if score.scored_at else None,
        } if score else None,
    }


# ── Signals ────────────────────────────────────────────────────────────────

@router.get("/signals")
async def list_signals(
    firm_id:     str | None = None,
    signal_type: str | None = None,
    limit:       int        = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = select(Signal).order_by(desc(Signal.captured_at)).limit(limit)
    if firm_id:
        q = q.where(Signal.firm_id == firm_id)
    if signal_type:
        q = q.where(Signal.type == signal_type)

    signals = (await db.execute(q)).scalars().all()
    return [
        {
            "id":          s.id,
            "firm_id":     s.firm_id,
            "type":        s.type,
            "value":       s.value,
            "text":        s.text,
            "source":      s.source,
            "captured_at": s.captured_at.isoformat(),
        }
        for s in signals
    ]