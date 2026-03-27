"""
Feature builder — aggregates the last 90 days of signals
for a firm into a flat feature vector for the ML model.
"""
import math
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Signal

LOOKBACK_DAYS = 90

FEATURE_NAMES = [
    "headcount_delta_90d",
    "headcount_signal_count",
    "funding_log_usd",
    "days_since_funding",
    "sentiment_mean",
    "sentiment_count",
    "sentiment_positive_ratio",
]


async def build_features(firm_id: str, db: AsyncSession) -> dict | None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    result = await db.execute(
        select(Signal)
        .where(Signal.firm_id == firm_id)
        .where(Signal.captured_at >= cutoff)
        .order_by(Signal.captured_at.desc())
    )
    signals = result.scalars().all()
    if not signals:
        return None

    hc       = [s.value for s in signals if s.type == "headcount_delta" and s.value]
    funding  = [s       for s in signals if s.type == "funding_round"   and s.value]
    senti    = [s.value for s in signals if s.type == "news_sentiment"  and s.value is not None]

    # Headcount
    hc_delta_90d = sum(hc)
    hc_count     = len(hc)

    # Funding (log-scale, millions)
    if funding:
        latest_usd  = max(s.value for s in funding)
        funding_log = math.log1p(latest_usd / 1_000_000) if latest_usd > 0 else 0.0
        latest_date = max(
            (s.captured_at.replace(tzinfo=timezone.utc)
             if s.captured_at.tzinfo is None else s.captured_at)
            for s in funding
        )
        days_since  = (datetime.now(timezone.utc) - latest_date).days
    else:
        funding_log = 0.0
        days_since  = LOOKBACK_DAYS

    # Sentiment
    if senti:
        senti_mean   = sum(senti) / len(senti)
        senti_pos    = len([v for v in senti if v > 0]) / len(senti)
    else:
        senti_mean   = 0.0
        senti_pos    = 0.0

    return {
        "headcount_delta_90d":       round(hc_delta_90d, 4),
        "headcount_signal_count":    hc_count,
        "funding_log_usd":           round(funding_log, 4),
        "days_since_funding":        days_since,
        "sentiment_mean":            round(senti_mean, 4),
        "sentiment_count":           len(senti),
        "sentiment_positive_ratio":  round(senti_pos, 4),
    }