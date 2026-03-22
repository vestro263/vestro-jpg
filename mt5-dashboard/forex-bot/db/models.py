"""
Database models — SQLAlchemy ORM for trade journal storage.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, DateTime,
    Boolean, Text, create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker
import os

Base = declarative_base()

DB_PATH = os.environ.get("BOT_DB_PATH", "db/trades.db")
ENGINE  = create_engine(f"sqlite:///{DB_PATH}", echo=False,
                        connect_args={"check_same_thread": False})
Session = sessionmaker(bind=ENGINE)


class Trade(Base):
    """One row per trade. Updated when trade closes."""
    __tablename__ = "trades"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    ticket        = Column(Integer, unique=True, index=True, nullable=False)
    symbol        = Column(String(20), nullable=False)
    direction     = Column(String(4),  nullable=False)   # "buy" | "sell"
    lot_size      = Column(Float,      nullable=False)
    entry_price   = Column(Float,      nullable=False)
    sl            = Column(Float)
    tp1           = Column(Float)
    tp2           = Column(Float)
    exit_price    = Column(Float)
    profit_usd    = Column(Float)
    profit_r      = Column(Float)       # profit in R multiples
    tss_score     = Column(Integer)
    checklist_score = Column(Integer)
    atr_zone      = Column(String(12))
    reason        = Column(Text)
    mistake       = Column(Text)        # manual field via API
    open_time     = Column(DateTime, default=datetime.utcnow)
    close_time    = Column(DateTime)
    is_open       = Column(Boolean, default=True)
    partial_closed = Column(Boolean, default=False)
    screenshot_path = Column(String(256))

    def to_dict(self):
        return {
            "id":             self.id,
            "ticket":         self.ticket,
            "symbol":         self.symbol,
            "direction":      self.direction,
            "lot_size":       self.lot_size,
            "entry_price":    self.entry_price,
            "sl":             self.sl,
            "tp1":            self.tp1,
            "tp2":            self.tp2,
            "exit_price":     self.exit_price,
            "profit_usd":     self.profit_usd,
            "profit_r":       self.profit_r,
            "tss_score":      self.tss_score,
            "checklist_score": self.checklist_score,
            "atr_zone":       self.atr_zone,
            "reason":         self.reason,
            "mistake":        self.mistake,
            "open_time":      str(self.open_time),
            "close_time":     str(self.close_time) if self.close_time else None,
            "is_open":        self.is_open,
        }


class DailyStats(Base):
    """Aggregated daily performance."""
    __tablename__ = "daily_stats"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    date          = Column(String(10), unique=True)   # YYYY-MM-DD
    total_trades  = Column(Integer, default=0)
    wins          = Column(Integer, default=0)
    losses        = Column(Integer, default=0)
    gross_profit  = Column(Float,   default=0.0)
    gross_loss    = Column(Float,   default=0.0)
    net_pnl       = Column(Float,   default=0.0)
    win_rate      = Column(Float,   default=0.0)
    avg_r         = Column(Float,   default=0.0)
    max_dd        = Column(Float,   default=0.0)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}