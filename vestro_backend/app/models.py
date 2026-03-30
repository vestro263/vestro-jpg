from sqlalchemy import (
    Column, String, Float, Integer, DateTime, Text,
    ForeignKey, Index, func
)
from sqlalchemy.orm import relationship
from app.database import Base
import uuid


def gen_id():
    return str(uuid.uuid4())


class Firm(Base):
    """One row per unique private firm discovered."""
    __tablename__ = "firms"

    id                = Column(String, primary_key=True, default=gen_id)
    name              = Column(String, nullable=False, index=True)
    domain            = Column(String, unique=True, index=True)   # dedup key
    sector            = Column(String)
    country           = Column(String)
    stage             = Column(String)                            # seed/series_a/b/c/growth
    employee_count    = Column(Integer)
    total_funding_usd = Column(Float)
    last_funding_date = Column(DateTime)
    crunchbase_url    = Column(String)
    created_at        = Column(DateTime, server_default=func.now())
    updated_at        = Column(DateTime, server_default=func.now(), onupdate=func.now())

    signals = relationship("Signal", back_populates="firm", cascade="all, delete-orphan")
    scores  = relationship("Score",  back_populates="firm", cascade="all, delete-orphan")


class Signal(Base):
    """
    Immutable event log — never update, only insert.
    type: headcount_delta | funding_round | news_sentiment | exec_departure
    """
    __tablename__ = "signals"

    id          = Column(String, primary_key=True, default=gen_id)
    firm_id     = Column(String, ForeignKey("firms.id", ondelete="CASCADE"), nullable=False)
    type        = Column(String, nullable=False)
    value       = Column(Float)          # numeric (delta, amount, sentiment score)
    text        = Column(Text)           # raw headline for sentiment signals
    source      = Column(String)         # crunchbase | linkedin | gdelt | google_news | rss
    captured_at = Column(DateTime, server_default=func.now(), index=True)

    firm = relationship("Firm", back_populates="signals")

    __table_args__ = (
        Index("ix_signals_firm_type",  "firm_id", "type"),
        Index("ix_signals_captured_at", "captured_at"),
    )


class Score(Base):
    """Latest ML prediction per firm. One row per firm, overwritten each run."""
    __tablename__ = "scores"

    id          = Column(String, primary_key=True, default=gen_id)
    firm_id     = Column(String, ForeignKey("firms.id", ondelete="CASCADE"),
                         nullable=False, unique=True)
    rise_prob   = Column(Float)          # 0.0 – 1.0
    fall_prob   = Column(Float)
    conviction  = Column(Integer)        # 0 – 100
    horizon_days= Column(Integer, default=90)
    top_driver  = Column(String)         # highest-weight SHAP feature name
    shap_json   = Column(Text)           # {"feature": shap_value, ...}
    scored_at   = Column(DateTime, server_default=func.now(), onupdate=func.now())

    firm = relationship("Firm", back_populates="scores")

    __table_args__ = (
        Index("ix_scores_conviction", "conviction"),
    )

class Credentials(Base):
    __tablename__ = "credentials"
    id          = Column(Integer, primary_key=True)
    user_id     = Column(String, unique=True, index=True)
    broker      = Column(String)           # "deriv" | "welltrade"
    login       = Column(String)           # encrypted
    password    = Column(String)           # encrypted
    server      = Column(String)           # encrypted
    api_token   = Column(String)           # encrypted (Deriv token)
    meta_account_id = Column(String)       # MetaApi account ID