"""
calibration_trainer.py
=======================
Trains calibrated ML models for each symbol.
- Base model: all executed+closed trades
- Per-regime models: executed+closed trades filtered by regime
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import (
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
)
from sqlalchemy import delete, select

from app.database import AsyncSessionLocal
from .signal_log_model import SignalLog, CalibrationConfig

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent / "models"
MODEL_DIR.mkdir(exist_ok=True)

MIN_SAMPLES        = 200
MIN_REGIME_SAMPLES = 50
TEST_SIZE          = 0.20
RANDOM_SEED        = 42
TARGET_HOLD_RATE   = 0.15
HOLD_TIGHTEN_STEPS = 20

FEATURES_V75 = [
    "rsi", "adx", "atr", "ema_50", "ema_200",
    "macd_hist", "tss_score", "checklist", "confidence",
]

FEATURES_CRASH500 = [
    "drop_spike", "recovery", "spike_score", "confidence",
]

STRATEGY_FEATURES = {
    "V75":      FEATURES_V75,
    "V25":      FEATURES_V75,
    "Crash500": FEATURES_CRASH500,
}

HARD_CODED_DEFAULTS = {
    "R_75": {
        "rsi_buy_max":    45.0, "rsi_sell_min":   55.0,
        "adx_min":        25.0, "tss_min":        2,
        "checklist_min":  3,    "confidence_min": 0.0,
        "spike_min":      None, "recovery_min":   None,
    },
    "R_25": {
        "rsi_buy_max":    50.0, "rsi_sell_min":   50.0,
        "adx_min":        20.0, "tss_min":        2,
        "checklist_min":  3,    "confidence_min": 0.0,
        "spike_min":      None, "recovery_min":   None,
    },
    "CRASH500": {
        "rsi_buy_max":    None, "rsi_sell_min":   None,
        "adx_min":        None, "tss_min":        None,
        "checklist_min":  None, "confidence_min": 0.0,
        "spike_min":      2.0,  "recovery_min":   0.3,
    },
}

SYMBOL_STRATEGY_MAP = {
    "R_75":     "V75",
    "R_25":     "V25",
    "CRASH500": "Crash500",
}

REGIMES = ["TREND", "RANGE", "HIGH_VOL", "CRASH"]


# =============================================================================
# Data loading
# =============================================================================

async def _load_rows(symbol: str) -> list[dict]:
    """Load all executed+closed trades for base model training."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SignalLog)
            .where(
                SignalLog.symbol    == symbol,
                SignalLog.label_15m != None,
                SignalLog.signal    != "HOLD",
                SignalLog.executed  == True,
                SignalLog.outcome.in_(["WIN", "LOSS"]),
            )
            .order_by(SignalLog.captured_at)
        )
        rows = result.scalars().all()

    wins   = sum(1 for r in rows if r.outcome == "WIN")
    losses = len(rows) - wins
    logger.info(
        f"[calibration_trainer] {symbol}: "
        f"loaded {len(rows)} executed+closed rows | "
        f"WIN={wins} LOSS={losses} "
        f"win_rate={round(wins/len(rows)*100,1) if rows else 0}%"
    )
    return [
        {k: v for k, v in r.__dict__.items() if not k.startswith("_")}
        for r in rows
    ]


async def _load_rows_for_regime(symbol: str, regime: str) -> list[dict]:
    """Load executed+closed trades for a specific regime."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SignalLog)
            .where(
                SignalLog.symbol    == symbol,
                SignalLog.label_15m != None,
                SignalLog.signal    != "HOLD",
                SignalLog.executed  == True,
                SignalLog.outcome.in_(["WIN", "LOSS"]),
                SignalLog.regime    == regime,
            )
            .order_by(SignalLog.captured_at)
        )
        rows = result.scalars().all()

    wins   = sum(1 for r in rows if r.outcome == "WIN")
    losses = len(rows) - wins
    logger.info(
        f"[calibration_trainer] {symbol}/{regime}: "
        f"{len(rows)} rows | WIN={wins} LOSS={losses} "
        f"win_rate={round(wins/len(rows)*100,1) if rows else 0}%"
    )
    return [
        {k: v for k, v in r.__dict__.items() if not k.startswith("_")}
        for r in rows
    ]


def _build_feature_matrix(
    rows:         list[dict],
    feature_cols: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    X_raw, y_raw = [], []
    n_dropped_missing = 0

    for r in rows:
        label = r.get("label_15m")
        if label is None:
            continue
        x = [r.get(f) for f in feature_cols]
        if sum(1 for v in x if v is None) > len(feature_cols) // 2:
            n_dropped_missing += 1
            continue
        X_raw.append(x)
        y_raw.append(label)

    if not X_raw:
        return np.array([]), np.array([])

    X = np.array(X_raw, dtype=float)
    y = np.array(y_raw, dtype=int)

    unique, counts = np.unique(y, return_counts=True)
    logger.info(
        f"[calibration_trainer] class distribution: "
        f"{dict(zip(unique.tolist(), counts.tolist()))} | "
        f"dropped {n_dropped_missing} missing-feature rows"
    )

    for col_idx in range(X.shape[1]):
        col    = X[:, col_idx]
        median = np.nanmedian(col)
        X[np.isnan(col), col_idx] = median

    return X, y


# =============================================================================
# Probability calibration
# =============================================================================

def _expected_calibration_error(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = 10,
) -> float:
    y_bin = (y_true == 1).astype(int)
    bins  = np.linspace(0.0, 1.0, n_bins + 1)
    ece   = 0.0
    n     = len(y_true)
    for i in range(n_bins):
        mask = (y_prob >= bins[i]) & (y_prob < bins[i + 1])
        if mask.sum() == 0:
            continue
        acc  = y_bin[mask].mean()
        conf = y_prob[mask].mean()
        ece += mask.sum() / n * abs(conf - acc)
    return float(ece)


def _fit_calibrated_model(
    base_model: GradientBoostingClassifier,
    X_train:    np.ndarray,
    y_train:    np.ndarray,
    X_test:     np.ndarray,
    y_test:     np.ndarray,
) -> tuple[object, str, float, float]:
    n_cal  = max(30, int(len(X_train) * 0.20))
    X_base = X_train[:-n_cal]
    y_base = y_train[:-n_cal]
    X_cal  = X_train[-n_cal:]
    y_cal  = y_train[-n_cal:]

    if len(X_base) < 10:
        return base_model, "none", 1.0, 1.0

    classes, counts = np.unique(y_base, return_counts=True)
    total           = len(y_base)
    w_map           = {c: total / (len(classes) * cnt) for c, cnt in zip(classes, counts)}
    sample_weights  = np.array([w_map[lbl] for lbl in y_base])

    base_refitted = GradientBoostingClassifier(
        n_estimators     = 200,
        max_depth        = 4,
        learning_rate    = 0.05,
        subsample        = 0.8,
        min_samples_leaf = 10,
        random_state     = RANDOM_SEED,
    )
    base_refitted.fit(X_base, y_base, sample_weight=sample_weights)

    results = {}
    for method in ("sigmoid", "isotonic"):
        try:
            cal = CalibratedClassifierCV(
                estimator = base_refitted,
                method    = method,
                cv        = "prefit",
            )
            cal.fit(X_cal, y_cal)

            classes_list = list(cal.classes_)
            win_idx      = classes_list.index(1) if 1 in classes_list else -1
            if win_idx < 0:
                continue

            proba   = cal.predict_proba(X_test)[:, win_idx]
            brier   = float(brier_score_loss((y_test == 1).astype(int), proba))
            ece_val = _expected_calibration_error(y_test, proba)

            logger.info(
                f"[calibration_trainer] method={method} "
                f"brier={brier:.4f} ece={ece_val:.4f}"
            )
            results[method] = (cal, brier, ece_val)

        except Exception as exc:
            logger.warning(f"[calibration_trainer] {method} failed: {exc}")

    if not results:
        return base_model, "none", 1.0, 1.0

    best_method = min(results, key=lambda m: (results[m][1], results[m][2]))
    best_model, best_brier, best_ece = results[best_method]
    logger.info(
        f"[calibration_trainer] selected method={best_method} "
        f"brier={best_brier:.4f} ece={best_ece:.4f}"
    )
    return best_model, best_method, best_brier, best_ece


# =============================================================================
# Threshold sweep
# =============================================================================

def _find_optimal_threshold(
    model,
    X_test:         np.ndarray,
    y_test:         np.ndarray,
    feature_idx:    int,
    feature_values: np.ndarray,
    direction:      str = "above",
    n_steps:        int = 40,
) -> float | None:
    best_thresh, best_f1 = None, -1.0
    thresholds = np.linspace(
        np.nanpercentile(feature_values, 5),
        np.nanpercentile(feature_values, 95),
        n_steps,
    )
    for thresh in thresholds:
        mask = feature_values >= thresh if direction == "above" else feature_values <= thresh
        if mask.sum() < 10:
            continue
        y_subset = y_test[mask]
        if len(np.unique(y_subset)) < 2:
            continue
        preds = model.predict(X_test[mask])
        score = float(f1_score(y_subset, preds, zero_division=0, average="binary", pos_label=1))
        if score > best_f1:
            best_f1, best_thresh = score, thresh
    return round(float(best_thresh), 4) if best_thresh is not None else None


# =============================================================================
# Hold budget enforcement
# =============================================================================

def _hold_rate_on_holdout(model, X_test, calibration, feature_cols) -> float:
    if len(X_test) == 0:
        return 0.0
    preds = model.predict(X_test)
    return float(np.sum(preds == -1) / len(preds))


def _enforce_hold_budget(model, X_test, y_test, calibration, feature_cols) -> dict:
    hold_rate = _hold_rate_on_holdout(model, X_test, calibration, feature_cols)
    logger.info(
        f"[calibration_trainer] initial hold rate: "
        f"{hold_rate:.1%} (target ≤ {TARGET_HOLD_RATE:.0%})"
    )
    if hold_rate <= TARGET_HOLD_RATE:
        return calibration

    for step in range(HOLD_TIGHTEN_STEPS):
        for key in ("adx_min", "tss_min", "checklist_min", "spike_min", "recovery_min", "confidence_min"):
            val = calibration.get(key)
            if val is not None and val > 0:
                calibration[key] = round(val * 0.95, 4)
        for key in ("rsi_buy_max",):
            val = calibration.get(key)
            if val is not None:
                calibration[key] = min(round(val * 1.05, 4), 60.0)
        for key in ("rsi_sell_min",):
            val = calibration.get(key)
            if val is not None:
                calibration[key] = max(round(val * 0.95, 4), 40.0)

        hold_rate = _hold_rate_on_holdout(model, X_test, calibration, feature_cols)
        if hold_rate <= TARGET_HOLD_RATE:
            logger.info(f"[calibration_trainer] hold budget achieved after {step+1} steps")
            break
    else:
        logger.warning(f"[calibration_trainer] hold budget not achieved — final rate={hold_rate:.1%}")

    return calibration


# =============================================================================
# Train base model for one symbol
# =============================================================================

async def train_symbol(
    symbol:        str,
    strategy_name: str,
) -> dict | None:
    logger.info(f"[calibration_trainer] loading rows for {symbol}...")
    rows = await _load_rows(symbol)

    if len(rows) < MIN_SAMPLES:
        logger.info(
            f"[calibration_trainer] {symbol}: {len(rows)} rows < {MIN_SAMPLES} — skipping"
        )
        return None

    feature_cols = STRATEGY_FEATURES.get(strategy_name, FEATURES_V75)
    X, y = _build_feature_matrix(rows, feature_cols)

    if len(X) < MIN_SAMPLES:
        logger.info(f"[calibration_trainer] {symbol}: too few usable rows after filtering")
        return None

    unique_classes = np.unique(y)
    if len(unique_classes) < 2:
        logger.warning(
            f"[calibration_trainer] {symbol}: "
            f"only one class {unique_classes.tolist()} — cannot train"
        )
        return None

    logger.info(f"[calibration_trainer] {symbol}: {len(X)} samples, features={len(feature_cols)}")

    split_idx       = int(len(X) * (1 - TEST_SIZE))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    classes, counts = np.unique(y_train, return_counts=True)
    total           = len(y_train)
    w_map           = {c: total / (len(classes) * cnt) for c, cnt in zip(classes, counts)}
    sample_weights  = np.array([w_map[lbl] for lbl in y_train])

    base_model = GradientBoostingClassifier(
        n_estimators     = 200,
        max_depth        = 4,
        learning_rate    = 0.05,
        subsample        = 0.8,
        min_samples_leaf = 20,
        random_state     = RANDOM_SEED,
    )
    base_model.fit(X_train, y_train, sample_weight=sample_weights)

    calibrated_model, cal_method, brier, ece = _fit_calibrated_model(
        base_model, X_train, y_train, X_test, y_test
    )

    y_pred    = calibrated_model.predict(X_test)
    precision = round(float(precision_score(y_test, y_pred, average="macro", zero_division=0)), 4)
    recall    = round(float(recall_score(   y_test, y_pred, average="macro", zero_division=0)), 4)
    f1        = round(float(f1_score(       y_test, y_pred, average="macro", zero_division=0)), 4)

    importances = {
        feat: round(float(imp), 4)
        for feat, imp in zip(feature_cols, base_model.feature_importances_)
    }

    logger.info(
        f"[calibration_trainer] {symbol}: "
        f"precision={precision} recall={recall} f1={f1} "
        f"cal={cal_method} brier={brier:.4f} ece={ece:.4f}"
    )

    model_path = MODEL_DIR / f"{symbol}_calibrated.pkl"
    joblib.dump(calibrated_model, model_path)
    logger.info(f"[calibration_trainer] {symbol}: base model saved → {model_path}")

    feat_idx = {f: i for i, f in enumerate(feature_cols)}

    def thresh(feat, direction="above"):
        if feat not in feat_idx:
            return None
        idx  = feat_idx[feat]
        vals = X_test[:, idx]
        return _find_optimal_threshold(calibrated_model, X_test, y_test, idx, vals, direction)

    calibration = {
        "symbol":   symbol,
        "strategy": strategy_name,
        "rsi_buy_max":    thresh("rsi",        direction="below"),
        "rsi_sell_min":   thresh("rsi",        direction="above"),
        "adx_min":        thresh("adx",        direction="above"),
        "tss_min":        int(thresh("tss_score",  direction="above") or 2),
        "checklist_min":  int(thresh("checklist",  direction="above") or 3),
        "confidence_min": thresh("confidence", direction="above"),
        "spike_min":      thresh("drop_spike", direction="above"),
        "recovery_min":   thresh("recovery",   direction="above"),
        "n_samples":               len(X),
        "precision":               precision,
        "recall":                  recall,
        "f1":                      f1,
        "feature_importance_json": json.dumps(importances),
    }

    defaults = HARD_CODED_DEFAULTS.get(symbol, {})
    for key, default_val in defaults.items():
        if calibration.get(key) is None and default_val is not None:
            calibration[key] = default_val

    calibration = _enforce_hold_budget(
        calibrated_model, X_test, y_test, calibration, feature_cols
    )
    return calibration


# =============================================================================
# Train per-regime model for one symbol+regime
# =============================================================================

async def train_symbol_regime(
    symbol:        str,
    strategy_name: str,
    regime:        str,
) -> bool:
    """Train and save a regime-specific model. Returns True if successful."""
    rows = await _load_rows_for_regime(symbol, regime)

    if len(rows) < MIN_REGIME_SAMPLES:
        logger.info(
            f"[calibration_trainer] {symbol}/{regime}: "
            f"{len(rows)} rows < {MIN_REGIME_SAMPLES} — skipping"
        )
        return False

    feature_cols = STRATEGY_FEATURES.get(strategy_name, FEATURES_V75)
    X, y = _build_feature_matrix(rows, feature_cols)

    if len(X) < MIN_REGIME_SAMPLES or len(np.unique(y)) < 2:
        logger.info(
            f"[calibration_trainer] {symbol}/{regime}: "
            f"insufficient data after filtering"
        )
        return False

    split_idx       = int(len(X) * (1 - TEST_SIZE))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    if len(X_train) < 10 or len(X_test) < 5:
        return False

    classes, counts = np.unique(y_train, return_counts=True)
    total           = len(y_train)
    w_map           = {c: total / (len(classes) * cnt) for c, cnt in zip(classes, counts)}
    sample_weights  = np.array([w_map[lbl] for lbl in y_train])

    base_model = GradientBoostingClassifier(
        n_estimators     = 200,
        max_depth        = 4,
        learning_rate    = 0.05,
        subsample        = 0.8,
        min_samples_leaf = 10,
        random_state     = RANDOM_SEED,
    )
    base_model.fit(X_train, y_train, sample_weight=sample_weights)

    calibrated_model, cal_method, brier, ece = _fit_calibrated_model(
        base_model, X_train, y_train, X_test, y_test
    )

    y_pred    = calibrated_model.predict(X_test)
    precision = round(float(precision_score(y_test, y_pred, average="macro", zero_division=0)), 4)
    f1        = round(float(f1_score(       y_test, y_pred, average="macro", zero_division=0)), 4)

    logger.info(
        f"[calibration_trainer] {symbol}/{regime}: "
        f"precision={precision} f1={f1} n={len(X)} "
        f"cal={cal_method} brier={brier:.4f}"
    )

    model_path = MODEL_DIR / f"{symbol}_{regime}_calibrated.pkl"
    joblib.dump(calibrated_model, model_path)
    logger.info(f"[calibration_trainer] saved → {model_path}")
    return True


# =============================================================================
# Write to DB
# =============================================================================

async def write_calibration(calibration: dict) -> None:
    symbol = calibration["symbol"]

    allowed_columns = {
        "symbol", "strategy",
        "rsi_buy_max", "rsi_sell_min",
        "adx_min", "tss_min", "checklist_min", "confidence_min",
        "spike_min", "recovery_min",
        "n_samples", "precision", "recall", "f1",
        "feature_importance_json",
    }

    row_data = {k: v for k, v in calibration.items() if k in allowed_columns}

    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(CalibrationConfig).where(CalibrationConfig.symbol == symbol)
        )
        row = CalibrationConfig(**row_data)
        db.add(row)
        await db.commit()

    logger.info(
        f"[calibration_trainer] wrote CalibrationConfig for {symbol} | "
        f"precision={calibration.get('precision')} "
        f"f1={calibration.get('f1')} "
        f"n_samples={calibration.get('n_samples')}"
    )


# =============================================================================
# Scheduler entry point
# =============================================================================

async def run_trainer(api_token: str = "") -> None:
    logger.info("[calibration_trainer] starting training run...")

    # Step 1 — base models
    for symbol, strategy_name in SYMBOL_STRATEGY_MAP.items():
        try:
            calibration = await train_symbol(symbol, strategy_name)
            if calibration:
                await write_calibration(calibration)
            else:
                logger.info(f"[calibration_trainer] {symbol}: no base model produced")
        except Exception as exc:
            logger.error(
                f"[calibration_trainer] base model failed for {symbol}: {exc}",
                exc_info=True,
            )

    # Step 2 — per-regime models (skip Crash500 for now)
    for symbol, strategy_name in SYMBOL_STRATEGY_MAP.items():
        if symbol == "CRASH500":
            continue
        for regime in REGIMES:
            try:
                ok = await train_symbol_regime(symbol, strategy_name, regime)
                if ok:
                    logger.info(
                        f"[calibration_trainer] regime model done: {symbol}/{regime}"
                    )
            except Exception as exc:
                logger.error(
                    f"[calibration_trainer] regime model failed {symbol}/{regime}: {exc}",
                    exc_info=True,
                )

    logger.info("[calibration_trainer] training run complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get("DERIV_API_TOKEN", "")
    asyncio.run(run_trainer(api_token=token))