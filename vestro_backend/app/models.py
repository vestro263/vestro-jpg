from sqlalchemy import (
    Column, String, Float, Integer, DateTime, Text,
    ForeignKey, Index, func, Boolean
)
from sqlalchemy.orm import relationship
from .database import Base
import uuid


def gen_id():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id             = Column(String, primary_key=True, default=gen_id)
    email          = Column(String, unique=True, nullable=False, index=True)
    name           = Column(String, nullable=True)
    avatar_url     = Column(String, nullable=True)
    active_account = Column(String, nullable=True)   # persisted active Deriv loginid
    created_at     = Column(DateTime, server_default=func.now())
    last_login     = Column(DateTime, server_default=func.now(), onupdate=func.now())

    credentials = relationship(
        "Credentials",
        foreign_keys="Credentials.google_user_id",
        primaryjoin="User.id == Credentials.google_user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Credentials(Base):
    __tablename__ = "credentials"

    id              = Column(Integer, primary_key=True)
    google_user_id  = Column(String, ForeignKey("users.id"), index=True)
    broker          = Column(String)

    account_id      = Column(String, index=True)   # ← NEW: Deriv loginid e.g. VRTC123, CR456
                                                    #   replaces the raw-DB-only user_id ghost

    login           = Column(String)               # keep — encrypted copy (backward compat)
    password        = Column(String)               # encrypted token
    server          = Column(String)
    api_token       = Column(String)               # encrypted token (same as password)
    meta_account_id = Column(String)

    is_demo         = Column(Boolean, default=False)   # NOW written on save
    is_active       = Column(Boolean, default=True)

    user = relationship(
        "User",
        foreign_keys=[google_user_id],
        back_populates="credentials",
    )


class Firm(Base):
    __tablename__ = "firms"

    id                = Column(String, primary_key=True, default=gen_id)
    name              = Column(String, nullable=False, index=True)
    domain            = Column(String, unique=True, index=True)
    sector            = Column(String)
    country           = Column(String)
    stage             = Column(String)
    employee_count    = Column(Integer)
    total_funding_usd = Column(Float)
    last_funding_date = Column(DateTime)
    crunchbase_url    = Column(String)
    created_at        = Column(DateTime, server_default=func.now())
    updated_at        = Column(DateTime, server_default=func.now(), onupdate=func.now())

    signals = relationship("Signal", back_populates="firm", cascade="all, delete-orphan")
    scores  = relationship("Score",  back_populates="firm", cascade="all, delete-orphan")


class Signal(Base):
    __tablename__ = "signals"

    id          = Column(String, primary_key=True, default=gen_id)
    firm_id     = Column(String, ForeignKey("firms.id", ondelete="CASCADE"), nullable=False)
    type        = Column(String, nullable=False)
    value       = Column(Float)
    text        = Column(Text)
    source      = Column(String)
    captured_at = Column(DateTime, server_default=func.now(), index=True)

    firm = relationship("Firm", back_populates="signals")

    __table_args__ = (
        Index("ix_signals_firm_type",   "firm_id", "type"),
        Index("ix_signals_captured_at", "captured_at"),
    )


class Score(Base):
    __tablename__ = "scores"

    id           = Column(String, primary_key=True, default=gen_id)
    firm_id      = Column(String, ForeignKey("firms.id", ondelete="CASCADE"),
                          nullable=False, unique=True)
    rise_prob    = Column(Float)
    fall_prob    = Column(Float)
    conviction   = Column(Integer)
    horizon_days = Column(Integer, default=90)
    top_driver   = Column(String)
    shap_json    = Column(Text)
    scored_at    = Column(DateTime, server_default=func.now(), onupdate=func.now())

    firm = relationship("Firm", back_populates="scores")

    __table_args__ = (
        Index("ix_scores_conviction", "conviction"),
    )