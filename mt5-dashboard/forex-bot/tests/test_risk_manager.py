"""
Tests for risk_manager.py — all trade approval rules.
"""

import sys, os, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from bridge.risk_manager import RiskManager, CORRELATION_GROUPS


@pytest.fixture
def config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def rm(config):
    return RiskManager(config)


@pytest.fixture
def good_signal():
    return {
        "direction":       1,
        "tss_score":       4,
        "checklist_score": 6,
        "atr_zone":        "normal",
        "atr":             0.0015,
        "ema21":           1.1050,
        "ema50":           1.1030,
        "ema200":          1.0900,
        "rsi":             38.0,
        "adx":             28.0,
        "macd_hist":       0.00003,
        "sl_distance":     0.00225,
        "tp1_distance":    0.003375,
        "tp2_distance":    0.00675,
        "reason":          "BUY | TSS=4",
    }


@pytest.fixture
def sym_info():
    return {
        "point":            0.00001,
        "digits":           5,
        "trade_tick_value": 1.0,
        "trade_tick_size":  0.00001,
        "volume_min":       0.01,
        "volume_max":       100.0,
        "volume_step":      0.01,
    }


# ── Approval tests ─────────────────────────────────────────────────────────
def test_good_signal_approved(rm, good_signal, sym_info):
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, [], "EURUSD", 0.00001, 1.0, sym_info
    )
    assert ok is True
    assert info["lot_size"] > 0
    assert info["sl"] < good_signal["ema21"]


def test_flat_direction_rejected(rm, good_signal, sym_info):
    good_signal["direction"] = 0
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, [], "EURUSD", 0.00001, 1.0, sym_info
    )
    assert ok is False
    assert "FLAT" in info["reason"]


def test_low_tss_rejected(rm, good_signal, sym_info):
    good_signal["tss_score"] = 2
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, [], "EURUSD", 0.00001, 1.0, sym_info
    )
    assert ok is False
    assert "TSS" in info["reason"]


def test_low_checklist_rejected(rm, good_signal, sym_info):
    good_signal["checklist_score"] = 3
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, [], "EURUSD", 0.00001, 1.0, sym_info
    )
    assert ok is False
    assert "Checklist" in info["reason"]


def test_extreme_atr_rejected(rm, good_signal, sym_info):
    good_signal["atr_zone"] = "extreme"
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, [], "EURUSD", 0.00001, 1.0, sym_info
    )
    assert ok is False
    assert "extreme" in info["reason"].lower()


def test_daily_loss_limit_rejected(rm, good_signal, sym_info):
    rm._daily_loss_usd = 600.0  # 6% of $10,000
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, [], "EURUSD", 0.00001, 1.0, sym_info
    )
    assert ok is False
    assert "Daily" in info["reason"]


def test_max_open_trades_rejected(rm, good_signal, sym_info):
    fake_positions = [
        {"symbol": "EURUSD", "type": "buy"},
        {"symbol": "GBPUSD", "type": "buy"},
        {"symbol": "USDJPY", "type": "sell"},
    ]
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, fake_positions, "AUDUSD", 0.00001, 1.0, sym_info
    )
    assert ok is False
    assert "Max" in info["reason"]


# ── SL / TP calculation tests ──────────────────────────────────────────────
def test_sl_tp_buy_direction(rm):
    sl, tp1, tp2 = rm.calc_sl_tp(1, 1.1000, 0.0015, 0.00001)
    assert sl  < 1.1000   # SL below entry for buy
    assert tp1 > 1.1000   # TP1 above entry for buy
    assert tp2 > tp1      # TP2 further than TP1


def test_sl_tp_sell_direction(rm):
    sl, tp1, tp2 = rm.calc_sl_tp(-1, 1.1000, 0.0015, 0.00001)
    assert sl  > 1.1000   # SL above entry for sell
    assert tp1 < 1.1000   # TP1 below entry for sell
    assert tp2 < tp1      # TP2 further than TP1


def test_tp2_rr_is_double_tp1_rr(rm):
    sl, tp1, tp2 = rm.calc_sl_tp(1, 1.1000, 0.0015, 0.00001)
    sl_dist  = abs(1.1000 - sl)
    tp1_dist = abs(tp1 - 1.1000)
    tp2_dist = abs(tp2 - 1.1000)
    assert abs(tp2_dist / tp1_dist - 2.0) < 0.01


# ── Lot size tests ─────────────────────────────────────────────────────────
def test_lot_size_scales_with_balance(rm):
    lot_1k  = rm.calc_lot_size(1_000,  0.0020, 1.0)
    lot_10k = rm.calc_lot_size(10_000, 0.0020, 1.0)
    assert abs(lot_10k / lot_1k - 10.0) < 0.1


def test_lot_size_scales_with_sl(rm):
    lot_tight = rm.calc_lot_size(10_000, 0.0010, 1.0)  # 10 pip SL
    lot_wide  = rm.calc_lot_size(10_000, 0.0020, 1.0)  # 20 pip SL
    assert lot_tight > lot_wide   # tighter SL = larger lot


def test_round_lot_respects_step(rm):
    lot = rm.round_lot(0.123, vol_min=0.01, vol_max=100.0, vol_step=0.01)
    assert lot == 0.12


def test_round_lot_clamps_to_min(rm):
    lot = rm.round_lot(0.001, vol_min=0.01, vol_max=100.0, vol_step=0.01)
    assert lot == 0.01


def test_round_lot_clamps_to_max(rm):
    lot = rm.round_lot(200.0, vol_min=0.01, vol_max=100.0, vol_step=0.01)
    assert lot == 100.0


# ── ATR zone tests ─────────────────────────────────────────────────────────
def test_atr_zones_correct(rm):
    assert rm.get_atr_zone(0.0005, 0.0020) == "low"
    assert rm.get_atr_zone(0.0020, 0.0020) == "normal"
    assert rm.get_atr_zone(0.0035, 0.0020) == "elevated"
    assert rm.get_atr_zone(0.0060, 0.0020) == "extreme"


def test_elevated_size_multiplier(rm):
    assert rm.get_size_multiplier("normal")   == 1.0
    assert rm.get_size_multiplier("low")      == 1.0
    assert rm.get_size_multiplier("elevated") == 0.5
    assert rm.get_size_multiplier("extreme")  == 0.0


# ── Correlation tests ──────────────────────────────────────────────────────
def test_eurusd_correlates_with_gbpusd(rm):
    corr = rm.get_correlated_symbols("EURUSD")
    assert "GBPUSD" in corr


def test_audusd_correlates_with_nzdusd(rm):
    corr = rm.get_correlated_symbols("AUDUSD")
    assert "NZDUSD" in corr


def test_xauusd_no_correlation(rm):
    corr = rm.get_correlated_symbols("XAUUSD")
    assert corr == []


# ── Daily reset test ───────────────────────────────────────────────────────
def test_daily_counter_records_loss(rm):
    rm.record_trade_result(-50.0)
    assert rm._daily_loss_usd == 50.0


def test_daily_counter_ignores_profit(rm):
    rm.record_trade_result(100.0)
    assert rm._daily_loss_usd == 0.0"""
Tests for risk_manager.py — all trade approval rules.
"""

import sys, os, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from bridge.risk_manager import RiskManager, CORRELATION_GROUPS


@pytest.fixture
def config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def rm(config):
    return RiskManager(config)


@pytest.fixture
def good_signal():
    return {
        "direction":       1,
        "tss_score":       4,
        "checklist_score": 6,
        "atr_zone":        "normal",
        "atr":             0.0015,
        "ema21":           1.1050,
        "ema50":           1.1030,
        "ema200":          1.0900,
        "rsi":             38.0,
        "adx":             28.0,
        "macd_hist":       0.00003,
        "sl_distance":     0.00225,
        "tp1_distance":    0.003375,
        "tp2_distance":    0.00675,
        "reason":          "BUY | TSS=4",
    }


@pytest.fixture
def sym_info():
    return {
        "point":            0.00001,
        "digits":           5,
        "trade_tick_value": 1.0,
        "trade_tick_size":  0.00001,
        "volume_min":       0.01,
        "volume_max":       100.0,
        "volume_step":      0.01,
    }


# ── Approval tests ─────────────────────────────────────────────────────────
def test_good_signal_approved(rm, good_signal, sym_info):
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, [], "EURUSD", 0.00001, 1.0, sym_info
    )
    assert ok is True
    assert info["lot_size"] > 0
    assert info["sl"] < good_signal["ema21"]


def test_flat_direction_rejected(rm, good_signal, sym_info):
    good_signal["direction"] = 0
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, [], "EURUSD", 0.00001, 1.0, sym_info
    )
    assert ok is False
    assert "FLAT" in info["reason"]


def test_low_tss_rejected(rm, good_signal, sym_info):
    good_signal["tss_score"] = 2
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, [], "EURUSD", 0.00001, 1.0, sym_info
    )
    assert ok is False
    assert "TSS" in info["reason"]


def test_low_checklist_rejected(rm, good_signal, sym_info):
    good_signal["checklist_score"] = 3
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, [], "EURUSD", 0.00001, 1.0, sym_info
    )
    assert ok is False
    assert "Checklist" in info["reason"]


def test_extreme_atr_rejected(rm, good_signal, sym_info):
    good_signal["atr_zone"] = "extreme"
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, [], "EURUSD", 0.00001, 1.0, sym_info
    )
    assert ok is False
    assert "extreme" in info["reason"].lower()


def test_daily_loss_limit_rejected(rm, good_signal, sym_info):
    rm._daily_loss_usd = 600.0  # 6% of $10,000
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, [], "EURUSD", 0.00001, 1.0, sym_info
    )
    assert ok is False
    assert "Daily" in info["reason"]


def test_max_open_trades_rejected(rm, good_signal, sym_info):
    fake_positions = [
        {"symbol": "EURUSD", "type": "buy"},
        {"symbol": "GBPUSD", "type": "buy"},
        {"symbol": "USDJPY", "type": "sell"},
    ]
    ok, info = rm.approve_trade(
        good_signal, 10_000.0, fake_positions, "AUDUSD", 0.00001, 1.0, sym_info
    )
    assert ok is False
    assert "Max" in info["reason"]


# ── SL / TP calculation tests ──────────────────────────────────────────────
def test_sl_tp_buy_direction(rm):
    sl, tp1, tp2 = rm.calc_sl_tp(1, 1.1000, 0.0015, 0.00001)
    assert sl  < 1.1000   # SL below entry for buy
    assert tp1 > 1.1000   # TP1 above entry for buy
    assert tp2 > tp1      # TP2 further than TP1


def test_sl_tp_sell_direction(rm):
    sl, tp1, tp2 = rm.calc_sl_tp(-1, 1.1000, 0.0015, 0.00001)
    assert sl  > 1.1000   # SL above entry for sell
    assert tp1 < 1.1000   # TP1 below entry for sell
    assert tp2 < tp1      # TP2 further than TP1


def test_tp2_rr_is_double_tp1_rr(rm):
    sl, tp1, tp2 = rm.calc_sl_tp(1, 1.1000, 0.0015, 0.00001)
    sl_dist  = abs(1.1000 - sl)
    tp1_dist = abs(tp1 - 1.1000)
    tp2_dist = abs(tp2 - 1.1000)
    assert abs(tp2_dist / tp1_dist - 2.0) < 0.01


# ── Lot size tests ─────────────────────────────────────────────────────────
def test_lot_size_scales_with_balance(rm):
    lot_1k  = rm.calc_lot_size(1_000,  0.0020, 1.0)
    lot_10k = rm.calc_lot_size(10_000, 0.0020, 1.0)
    assert abs(lot_10k / lot_1k - 10.0) < 0.1


def test_lot_size_scales_with_sl(rm):
    lot_tight = rm.calc_lot_size(10_000, 0.0010, 1.0)  # 10 pip SL
    lot_wide  = rm.calc_lot_size(10_000, 0.0020, 1.0)  # 20 pip SL
    assert lot_tight > lot_wide   # tighter SL = larger lot


def test_round_lot_respects_step(rm):
    lot = rm.round_lot(0.123, vol_min=0.01, vol_max=100.0, vol_step=0.01)
    assert lot == 0.12


def test_round_lot_clamps_to_min(rm):
    lot = rm.round_lot(0.001, vol_min=0.01, vol_max=100.0, vol_step=0.01)
    assert lot == 0.01


def test_round_lot_clamps_to_max(rm):
    lot = rm.round_lot(200.0, vol_min=0.01, vol_max=100.0, vol_step=0.01)
    assert lot == 100.0


# ── ATR zone tests ─────────────────────────────────────────────────────────
def test_atr_zones_correct(rm):
    assert rm.get_atr_zone(0.0005, 0.0020) == "low"
    assert rm.get_atr_zone(0.0020, 0.0020) == "normal"
    assert rm.get_atr_zone(0.0035, 0.0020) == "elevated"
    assert rm.get_atr_zone(0.0060, 0.0020) == "extreme"


def test_elevated_size_multiplier(rm):
    assert rm.get_size_multiplier("normal")   == 1.0
    assert rm.get_size_multiplier("low")      == 1.0
    assert rm.get_size_multiplier("elevated") == 0.5
    assert rm.get_size_multiplier("extreme")  == 0.0


# ── Correlation tests ──────────────────────────────────────────────────────
def test_eurusd_correlates_with_gbpusd(rm):
    corr = rm.get_correlated_symbols("EURUSD")
    assert "GBPUSD" in corr


def test_audusd_correlates_with_nzdusd(rm):
    corr = rm.get_correlated_symbols("AUDUSD")
    assert "NZDUSD" in corr


def test_xauusd_no_correlation(rm):
    corr = rm.get_correlated_symbols("XAUUSD")
    assert corr == []


# ── Daily reset test ───────────────────────────────────────────────────────
def test_daily_counter_records_loss(rm):
    rm.record_trade_result(-50.0)
    assert rm._daily_loss_usd == 50.0


def test_daily_counter_ignores_profit(rm):
    rm.record_trade_result(100.0)
    assert rm._daily_loss_usd == 0.0