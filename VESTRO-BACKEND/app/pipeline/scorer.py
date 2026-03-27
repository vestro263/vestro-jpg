"""
ML scorer.
• Loads trained model from ml/model.pkl if it exists.
• Falls back to a calibrated heuristic scorer until you have labelled data.
• Writes/overwrites a Score row per firm after every signal batch.
• Broadcasts the new score to connected WebSocket clients.
"""
import json
import logging
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Firm, Score
from app.pipeline.features import build_features, FEATURE_NAMES

log = logging.getLogger(__name__)

MODEL_PATH     = Path(__file__).parent.parent.parent / "ml" / "model.pkl"
EXPLAINER_PATH = Path(__file__).parent.parent.parent / "ml" / "shap_explainer.pkl"

_model     = None
_explainer = None
_loaded    = False


def _load():
    global _model, _explainer, _loaded
    if _loaded:
        return
    _loaded = True
    if MODEL_PATH.exists():
        import joblib
        _model = joblib.load(MODEL_PATH)
        log.info("Loaded gradient boost model")
        if EXPLAINER_PATH.exists():
            _explainer = joblib.load(EXPLAINER_PATH)
            log.info("Loaded SHAP explainer")
    else:
        log.warning("No model.pkl — using heuristic scorer")


# ── Heuristic scorer (pre-training bootstrap) ─────────────────────────────

def _heuristic(f: dict) -> tuple[float, float]:
    score = 0.50
    hc = f.get("headcount_delta_90d", 0)
    score += 0.18 if hc > 0.25 else 0.09 if hc > 0.05 else (-0.15 if hc < -0.10 else 0)

    fund = f.get("funding_log_usd", 0)
    score += 0.10 if fund > 3 else 0.05 if fund > 1 else 0

    score += f.get("sentiment_mean", 0) * 0.12
    score += (f.get("sentiment_positive_ratio", 0.5) - 0.5) * 0.08

    dsf = f.get("days_since_funding", 90)
    score += 0.05 if dsf < 30 else (-0.05 if dsf > 180 else 0)

    rise = round(max(0.05, min(0.95, score)), 3)
    return rise, round(1.0 - rise, 3)


# ── ML scorer ─────────────────────────────────────────────────────────────

def _ml_score(f: dict) -> tuple[float, float, dict]:
    import pandas as pd
    X     = pd.DataFrame([f])[FEATURE_NAMES].fillna(0)
    probs = _model.predict_proba(X)[0]
    rise, fall = round(float(probs[1]), 3), round(float(probs[0]), 3)

    shap_dict = {}
    if _explainer:
        try:
            vals = _explainer(X).values[0]
            shap_dict = {n: round(float(v), 4) for n, v in zip(FEATURE_NAMES, vals)}
        except Exception as e:
            log.debug(f"SHAP error: {e}")

    return rise, fall, shap_dict


def _top_driver(shap: dict, features: dict) -> str:
    if shap:
        return max(shap, key=lambda k: abs(shap[k]))
    # fallback: largest abs feature value
    return max(features, key=lambda k: abs(features[k])) if features else "unknown"


# ── Public API ─────────────────────────────────────────────────────────────

async def score_firm(firm_id: str, db: AsyncSession) -> Score | None:
    _load()
    features = await build_features(firm_id, db)
    if not features:
        return None

    shap_dict: dict = {}
    if _model:
        rise, fall, shap_dict = _ml_score(features)
    else:
        rise, fall = _heuristic(features)

    conviction = int(max(rise, fall) * 100)
    top        = _top_driver(shap_dict, features)

    result = await db.execute(select(Score).where(Score.firm_id == firm_id))
    score  = result.scalar_one_or_none()

    if score:
        score.rise_prob  = rise
        score.fall_prob  = fall
        score.conviction = conviction
        score.top_driver = top
        score.shap_json  = json.dumps(shap_dict)
    else:
        score = Score(
            firm_id=firm_id, rise_prob=rise, fall_prob=fall,
            conviction=conviction, top_driver=top,
            shap_json=json.dumps(shap_dict),
        )
        db.add(score)

    await db.commit()

    # Broadcast to WebSocket clients
    from app.routes.stream import broadcast
    await broadcast({
        "type": "score_update",
        "firm_id": firm_id,
        "rise_prob": rise,
        "fall_prob": fall,
        "conviction": conviction,
        "top_driver": top,
    })

    return score


async def score_all_firms(db: AsyncSession):
    result = await db.execute(select(Firm))
    firms  = result.scalars().all()
    log.info(f"Scoring {len(firms)} firms")
    for firm in firms:
        try:
            await score_firm(firm.id, db)
        except Exception as e:
            log.error(f"Score failed {firm.name}: {e}")