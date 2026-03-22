"""
Tests for db/journal.py — trade logging and performance stats.
Uses an in-memory SQLite database so no file is created.
"""

import sys, os, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Override DB path to in-memory for testing
os.environ["BOT_DB_PATH"] = ":memory:"

from db.models import Base, ENGINE, Session
from db.journal import (
    init_db, log_trade, update_trade_exit,
    get_recent_trades, get_performance_stats,
    get_open_journal_trades, get_trade_by_ticket,
)


@pytest.fixture(autouse=True)
def fresh_db():
    """Re-create tables before each test."""
    Base.metadata.drop_all(ENGINE)
    Base.metadata.create_all(ENGINE)
    yield
    Base.metadata.drop_all(ENGINE)


def _insert_trade(ticket=1001, symbol="EURUSD", direction="buy",
                  profit=None, entry=1.1000, sl=1.0970, tp1=1.1045, tp2=1.1090):
    log_trade(
        ticket=ticket, symbol=symbol, direction=direction,
        lot_size=0.10, entry=entry, sl=sl, tp1=tp1, tp2=tp2,
        tss_score=4, checklist=6, reason="TEST", atr_zone="normal",
    )
    if profit is not None:
        update_trade_exit(ticket, exit_price=tp2 if profit > 0 else sl,
                          profit_usd=profit)


# ── log_trade ──────────────────────────────────────────────────────────────
def test_log_trade_creates_record():
    _insert_trade(ticket=1001)
    trades = get_recent_trades(10)
    assert len(trades) == 1
    assert trades[0]["ticket"] == 1001
    assert trades[0]["symbol"] == "EURUSD"
    assert trades[0]["is_open"] is True


def test_log_trade_fields_stored():
    _insert_trade(ticket=1002, direction="sell")
    t = get_trade_by_ticket(1002)
    assert t["direction"] == "sell"
    assert t["tss_score"] == 4
    assert t["checklist_score"] == 6
    assert t["atr_zone"] == "normal"


# ── update_trade_exit ──────────────────────────────────────────────────────
def test_update_exit_marks_closed():
    _insert_trade(ticket=1003)
    update_trade_exit(1003, exit_price=1.1090, profit_usd=90.0)
    t = get_trade_by_ticket(1003)
    assert t["is_open"] is False
    assert t["profit_usd"] == 90.0
    assert t["exit_price"] == 1.1090


def test_update_exit_calculates_r():
    _insert_trade(ticket=1004, entry=1.1000, sl=1.0970, tp1=1.1045, tp2=1.1090)
    update_trade_exit(1004, exit_price=1.1090, profit_usd=90.0)
    t = get_trade_by_ticket(1004)
    assert t["profit_r"] is not None
    assert t["profit_r"] > 0


def test_update_exit_loss():
    _insert_trade(ticket=1005)
    update_trade_exit(1005, exit_price=1.0970, profit_usd=-30.0)
    t = get_trade_by_ticket(1005)
    assert t["profit_usd"] == -30.0
    assert t["is_open"] is False


# ── get_open_journal_trades ───────────────────────────────────────────────
def test_get_open_trades_filters_correctly():
    _insert_trade(ticket=2001)
    _insert_trade(ticket=2002, profit=50.0)
    open_trades = get_open_journal_trades()
    assert len(open_trades) == 1
    assert open_trades[0]["ticket"] == 2001


# ── get_performance_stats ──────────────────────────────────────────────────
def test_performance_stats_empty():
    stats = get_performance_stats(30)
    assert "message" in stats or "total_trades" in stats


def test_performance_stats_win_rate():
    _insert_trade(ticket=3001, profit=100.0)
    _insert_trade(ticket=3002, profit=80.0)
    _insert_trade(ticket=3003, profit=-30.0)
    stats = get_performance_stats(365)
    assert stats["total_trades"] == 3
    assert stats["wins"] == 2
    assert stats["losses"] == 1
    assert abs(stats["win_rate"] - 66.7) < 1.0


def test_performance_stats_net_pnl():
    _insert_trade(ticket=4001, profit=100.0)
    _insert_trade(ticket=4002, profit=-40.0)
    stats = get_performance_stats(365)
    assert stats["net_pnl_usd"] == 60.0


def test_performance_stats_expectancy_positive():
    """With 2 wins of +2R and 1 loss of -1R, expectancy should be positive."""
    for i, (profit, exit_px) in enumerate([(200, 1.1090), (200, 1.1090), (-30, 1.0970)]):
        _insert_trade(ticket=5000 + i, profit=profit, entry=1.1000, sl=1.0970)
    stats = get_performance_stats(365)
    assert stats["expectancy_usd"] > 0


def test_performance_stats_best_worst():
    _insert_trade(ticket=6001, profit=300.0)
    _insert_trade(ticket=6002, profit=-50.0)
    _insert_trade(ticket=6003, profit=100.0)
    stats = get_performance_stats(365)
    assert stats["best_trade"]  == 300.0
    assert stats["worst_trade"] == -50.0


# ── Multiple symbols ──────────────────────────────────────────────────────
def test_multiple_symbols():
    _insert_trade(ticket=7001, symbol="EURUSD", profit=100.0)
    _insert_trade(ticket=7002, symbol="GBPUSD", profit=-20.0)
    _insert_trade(ticket=7003, symbol="USDJPY", profit=50.0)
    trades = get_recent_trades(10)
    syms = {t["symbol"] for t in trades}
    assert syms == {"EURUSD", "GBPUSD", "USDJPY"}


# ── Ticket not found ──────────────────────────────────────────────────────
def test_get_trade_by_ticket_missing():
    result = get_trade_by_ticket(99999)
    assert result is None


def test_update_exit_missing_ticket():
    # Should not raise, just log a warning
    update_trade_exit(99999, exit_price=1.1000, profit_usd=0.0)