"""
Journal — CRUD operations for the trade journal database.
Implements performance metrics from Section 8 of the strategy.
"""

import logging
from datetime import datetime, date
from typing import List, Optional

from db.models import Base, ENGINE, Session, Trade, DailyStats

logger = logging.getLogger(__name__)


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(ENGINE)
    logger.info("Database initialized.")


# ── Write ──────────────────────────────────────────────────────────────────
def log_trade(
    ticket:     int,
    symbol:     str,
    direction:  str,
    lot_size:   float,
    entry:      float,
    sl:         float,
    tp1:        float,
    tp2:        float,
    tss_score:  int   = 0,
    checklist:  int   = 0,
    reason:     str   = "",
    atr_zone:   str   = "normal",
):
    """Insert a new open trade into the journal."""
    session = Session()
    try:
        trade = Trade(
            ticket          = ticket,
            symbol          = symbol,
            direction       = direction,
            lot_size        = lot_size,
            entry_price     = entry,
            sl              = sl,
            tp1             = tp1,
            tp2             = tp2,
            tss_score       = tss_score,
            checklist_score = checklist,
            reason          = reason,
            atr_zone        = atr_zone,
            open_time       = datetime.utcnow(),
            is_open         = True,
        )
        session.add(trade)
        session.commit()
        logger.info(f"Trade logged: ticket={ticket} {symbol} {direction}")
    except Exception as e:
        session.rollback()
        logger.error(f"log_trade error: {e}")
    finally:
        session.close()


def update_trade_exit(
    ticket:      int,
    exit_price:  float,
    profit_usd:  float,
    mistake:     str = "",
):
    """Mark a trade as closed with exit data."""
    session = Session()
    try:
        trade = session.query(Trade).filter_by(ticket=ticket).first()
        if not trade:
            logger.warning(f"Trade {ticket} not found in journal")
            return

        sl_dist = abs(trade.entry_price - trade.sl) if trade.sl else 1
        trade.exit_price = exit_price
        trade.profit_usd = profit_usd
        trade.profit_r   = profit_usd / (sl_dist * trade.lot_size * 10_000) \
                           if sl_dist > 0 else 0.0
        trade.close_time = datetime.utcnow()
        trade.is_open    = False
        trade.mistake    = mistake
        session.commit()
        _update_daily_stats(session, trade)
        logger.info(f"Trade exit logged: ticket={ticket} PnL=${profit_usd:.2f}")
    except Exception as e:
        session.rollback()
        logger.error(f"update_trade_exit error: {e}")
    finally:
        session.close()


def _update_daily_stats(session, trade: Trade):
    today = date.today().isoformat()
    stats = session.query(DailyStats).filter_by(date=today).first()
    if not stats:
        stats = DailyStats(date=today)
        session.add(stats)

    stats.total_trades += 1
    if trade.profit_usd >= 0:
        stats.wins        += 1
        stats.gross_profit += trade.profit_usd
    else:
        stats.losses      += 1
        stats.gross_loss  += abs(trade.profit_usd)

    stats.net_pnl  = stats.gross_profit - stats.gross_loss
    stats.win_rate = stats.wins / stats.total_trades if stats.total_trades else 0

    # Average R
    all_trades = session.query(Trade).filter(
        Trade.close_time >= datetime.utcnow().replace(hour=0, minute=0)
    ).all()
    rs = [t.profit_r for t in all_trades if t.profit_r is not None]
    stats.avg_r = sum(rs) / len(rs) if rs else 0.0

    session.commit()


# ── Read ───────────────────────────────────────────────────────────────────
def get_recent_trades(limit: int = 50) -> list:
    session = Session()
    try:
        trades = session.query(Trade)\
                        .order_by(Trade.open_time.desc())\
                        .limit(limit).all()
        return [t.to_dict() for t in trades]
    finally:
        session.close()


def get_open_journal_trades() -> list:
    session = Session()
    try:
        trades = session.query(Trade).filter_by(is_open=True).all()
        return [t.to_dict() for t in trades]
    finally:
        session.close()


def get_trade_by_ticket(ticket: int) -> Optional[dict]:
    session = Session()
    try:
        t = session.query(Trade).filter_by(ticket=ticket).first()
        return t.to_dict() if t else None
    finally:
        session.close()


def get_performance_stats(days: int = 30) -> dict:
    """
    Return performance metrics for the last N days.
    Implements the Section 8 weekly review metrics.
    """
    session = Session()
    try:
        from sqlalchemy import func
        cutoff = datetime.utcnow().replace(
            hour=0, minute=0, second=0
        )
        # Subtract days
        from datetime import timedelta
        cutoff -= timedelta(days=days)

        closed = session.query(Trade).filter(
            Trade.is_open    == False,
            Trade.close_time >= cutoff,
        ).all()

        if not closed:
            return {"message": "No closed trades in period", "days": days}

        profits  = [t.profit_usd for t in closed if t.profit_usd is not None]
        rs       = [t.profit_r   for t in closed if t.profit_r   is not None]
        wins     = [p for p in profits if p >= 0]
        losses   = [p for p in profits if p <  0]

        win_rate = len(wins) / len(profits) if profits else 0
        avg_win  = sum(wins)   / len(wins)   if wins   else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        # Expectancy = (Win% × AvgWin) − (Loss% × AvgLoss)
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * abs(avg_loss))

        # Max drawdown
        balance   = 0.0
        peak      = 0.0
        max_dd    = 0.0
        for p in sorted(profits):
            balance += p
            peak     = max(peak, balance)
            dd       = (peak - balance) / peak if peak > 0 else 0
            max_dd   = max(max_dd, dd)

        return {
            "period_days":    days,
            "total_trades":   len(closed),
            "wins":           len(wins),
            "losses":         len(losses),
            "win_rate":       round(win_rate * 100, 1),
            "avg_win_usd":    round(avg_win, 2),
            "avg_loss_usd":   round(avg_loss, 2),
            "net_pnl_usd":    round(sum(profits), 2),
            "expectancy_usd": round(expectancy, 2),
            "avg_r":          round(sum(rs) / len(rs), 2) if rs else 0,
            "max_drawdown_pct": round(max_dd * 100, 2),
            "best_trade":     round(max(profits), 2) if profits else 0,
            "worst_trade":    round(min(profits), 2) if profits else 0,
        }
    finally:
        session.close()


def get_daily_stats(limit: int = 30) -> list:
    session = Session()
    try:
        rows = session.query(DailyStats)\
                      .order_by(DailyStats.date.desc())\
                      .limit(limit).all()
        return [r.to_dict() for r in rows]
    finally:
        session.close()