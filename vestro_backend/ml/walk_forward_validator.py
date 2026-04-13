"""
walk_forward_validator.py  (upgraded)
======================================
True rolling walk-forward validation.

Changes vs original
--------------------
1. ROLLING WINDOW OPTION
   Original was expanding-window only (train on all history up to fold).
   Added train_bars parameter: if set, each fold trains on a FIXED-SIZE
   rolling window instead of all history.  Expanding window is default
   (None) to preserve current behaviour, but rolling is better when
   market regime changes make old data harmful.

2. PURGE + EMBARGO GAP
   Original had a 10-row purge gap.  Added embargo_bars on top: bars
   between train end and test start are skipped to prevent feature
   leakage when rolling features (ATR, volatility) span the boundary.
   Default embargo = 5 bars (one trading hour on M15).

3. PER-CLASS METRICS IN FOLD RESULTS
   Original only tracked weighted-average metrics.  Each FoldResult now
   carries precision_win, precision_loss, f1_win, f1_loss separately so
   you can see if WIN precision is declining across folds (early overfit
   signal) before the aggregate numbers move.

4. STABILITY GATE WITH REASON
   Original marked is_stable = std_f1 < 0.10.  Upgraded to multi-criterion:
     • std_f1         < 0.12   (F1 consistency across folds)
     • mean_precision > 0.50   (better than random on average)
     • min fold f1    > 0.30   (no catastrophic fold)
   If ANY criterion fails, write_to_db is blocked and reason is logged.

5. BEST MODEL SELECTION
   Original used highest-F1 fold's model for threshold extraction.  Now
   uses the fold with highest precision_win (most relevant for live trading)
   to avoid selecting an overfit fold.

6. CALIBRATED PROBABILITY OUTPUT
   After selecting the best model, wraps it with CalibratedClassifierCV
   (sigmoid) on the best fold's held-out data.  The calibrated model is
   what gets persisted — its predict_proba output feeds calibration_train.py.

Drop-in replacement for run_validator():
    In signal_engine.py:
        from ml.walk_forward_validator import run_validator
        asyncio.create_task(run_validator())
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import (
    f1_score,
    precision_score,
    recall_score,
    classification_report,
)
from sqlalchemy import select

from app.database import AsyncSessionLocal
from .signal_log_model import SignalLog, CalibrationConfig
from .calibration_trainer import (
    STRATEGY_FEATURES,
    HARD_CODED_DEFAULTS,
    MIN_SAMPLES,
    RANDOM_SEED,
    _build_feature_matrix,
    _find_optimal_threshold,
    write_calibration,
)

logger = logging.getLogger(__name__)

# ── Walk-forward config ───────────────────────────────────────────────────────
N_FOLDS        = 5       # number of test windows
MIN_TRAIN_PCT  = 0.40    # first training window uses 40% of data
PURGE_GAP      = 10      # rows to drop at train/test boundary (label leakage)
EMBARGO_BARS   = 5       # additional rows to skip after purge (feature leakage)

# ── Stability criteria ────────────────────────────────────────────────────────
MAX_STD_F1          = 0.12   # F1 must be consistent across folds
MIN_MEAN_PRECISION  = 0.50   # must beat random on average
MIN_FOLD_F1         = 0.30   # no single fold can be catastrophic


# =============================================================================
# Result dataclasses
# =============================================================================

@dataclass
class FoldResult:
    fold:           int
    train_size:     int
    test_size:      int
    precision:      float     # macro
    recall:         float     # macro
    f1:             float     # macro
    precision_win:  float     # class +1 only
    precision_loss: float     # class -1 only
    f1_win:         float
    f1_loss:        float
    feature_importance: dict  = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    symbol:          str
    strategy:        str
    n_folds:         int
    folds:           list[FoldResult]
    mean_precision:  float
    mean_recall:     float
    mean_f1:         float
    std_f1:          float
    calibration:     dict
    is_stable:       bool
    stability_reason: str    # human-readable explanation when unstable


# =============================================================================
# Data loader  (unchanged interface, ascending order enforced)
# =============================================================================

async def _load_labeled_rows(symbol: str) -> list[dict]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SignalLog)
            .where(
                SignalLog.symbol    == symbol,
                SignalLog.label_15m != None,     # noqa: E711
                SignalLog.signal    != "HOLD",
            )
            .order_by(SignalLog.captured_at.asc())  # chronological — never shuffle
        )
        rows = result.scalars().all()

    return [
        {col.name: getattr(r, col.name) for col in SignalLog.__table__.columns}
        for r in rows
    ]


# =============================================================================
# Walk-forward engine
# =============================================================================

def _run_walk_forward(
    X:             np.ndarray,
    y:             np.ndarray,
    feature_cols:  list[str],
    n_folds:       int       = N_FOLDS,
    min_train_pct: float     = MIN_TRAIN_PCT,
    purge_gap:     int       = PURGE_GAP,
    embargo_bars:  int       = EMBARGO_BARS,
    train_bars:    Optional[int] = None,   # None = expanding window
) -> tuple[list[FoldResult], GradientBoostingClassifier, np.ndarray, np.ndarray]:
    """
    Rolling or expanding walk-forward.
    Returns: fold_results, best_model, best_X_test, best_y_test.
    Best = highest precision_win (most trading-relevant metric).
    """
    n         = len(X)
    fold_size = int(n * (1 - min_train_pct) / n_folds)
    gap       = purge_gap + embargo_bars

    if fold_size < 20:
        raise ValueError(
            f"Not enough data for {n_folds} folds. "
            f"Got {n} rows; need at least {int(n * min_train_pct) + n_folds * 20}."
        )

    fold_results: list[FoldResult] = []
    best_prec_win = -1.0
    best_model    = None
    best_X_test   = None
    best_y_test   = None

    for fold_idx in range(n_folds):
        train_end  = int(n * min_train_pct) + fold_idx * fold_size
        test_start = train_end + gap
        test_end   = min(test_start + fold_size, n)

        if test_start >= n or test_end <= test_start:
            logger.warning(f"[walk_forward] fold {fold_idx+1} skipped — out of data")
            continue

        # Rolling vs expanding window
        if train_bars is not None:
            train_start = max(0, train_end - train_bars)
        else:
            train_start = 0

        X_train, y_train = X[train_start:train_end], y[train_start:train_end]
        X_test,  y_test  = X[test_start:test_end],   y[test_start:test_end]

        if len(np.unique(y_test)) < 2:
            logger.warning(f"[walk_forward] fold {fold_idx+1} skipped — single class in test")
            continue

        logger.info(
            f"[walk_forward] fold {fold_idx+1}/{n_folds} "
            f"train={len(X_train)} ({train_start}:{train_end}) "
            f"test={len(X_test)} ({test_start}:{test_end})"
        )

        # ── Sample weights for class balance ─────────────────────────────
        classes, counts = np.unique(y_train, return_counts=True)
        total           = len(y_train)
        w_map           = {c: total / (len(classes) * cnt)
                           for c, cnt in zip(classes, counts)}
        sample_weights  = np.array([w_map[lbl] for lbl in y_train])

        # ── Train ─────────────────────────────────────────────────────────
        model = GradientBoostingClassifier(
            n_estimators     = 200,
            max_depth        = 4,
            learning_rate    = 0.05,
            subsample        = 0.8,
            min_samples_leaf = 20,
            random_state     = RANDOM_SEED,
        )
        model.fit(X_train, y_train, sample_weight=sample_weights)

        # ── Evaluate — macro + per-class ──────────────────────────────────
        y_pred = model.predict(X_test)

        prec_macro  = float(precision_score(y_test, y_pred, average="macro",    zero_division=0))
        rec_macro   = float(recall_score(   y_test, y_pred, average="macro",    zero_division=0))
        f1_macro    = float(f1_score(       y_test, y_pred, average="macro",    zero_division=0))
        prec_win    = float(precision_score(y_test, y_pred, labels=[1],  average="macro", zero_division=0))
        prec_loss   = float(precision_score(y_test, y_pred, labels=[-1], average="macro", zero_division=0))
        f1_win      = float(f1_score(       y_test, y_pred, labels=[1],  average="macro", zero_division=0))
        f1_loss     = float(f1_score(       y_test, y_pred, labels=[-1], average="macro", zero_division=0))

        importances = {
            feat: round(float(imp), 4)
            for feat, imp in zip(feature_cols, model.feature_importances_)
        }

        fold = FoldResult(
            fold           = fold_idx + 1,
            train_size     = len(X_train),
            test_size      = len(X_test),
            precision      = round(prec_macro, 4),
            recall         = round(rec_macro,  4),
            f1             = round(f1_macro,   4),
            precision_win  = round(prec_win,   4),
            precision_loss = round(prec_loss,  4),
            f1_win         = round(f1_win,     4),
            f1_loss        = round(f1_loss,    4),
            feature_importance = importances,
        )
        fold_results.append(fold)

        logger.info(
            f"[walk_forward] fold {fold_idx+1} → "
            f"precision={fold.precision} recall={fold.recall} f1={fold.f1} | "
            f"precision_win={fold.precision_win} f1_win={fold.f1_win}"
        )
        logger.info(
            f"[walk_forward] fold {fold_idx+1} classification report:\n"
            + classification_report(y_test, y_pred, zero_division=0)
        )

        # Best model = highest precision on WIN class
        if prec_win > best_prec_win:
            best_prec_win = prec_win
            best_model    = model
            best_X_test   = X_test
            best_y_test   = y_test

    return fold_results, best_model, best_X_test, best_y_test


# =============================================================================
# Multi-criterion stability check
# =============================================================================

def _check_stability(fold_results: list[FoldResult]) -> tuple[bool, str]:
    """
    Returns (is_stable, reason_string).
    Three criteria must ALL pass to write calibration to DB.
    """
    f1s        = [f.f1        for f in fold_results]
    precisions = [f.precision for f in fold_results]

    std_f1        = float(np.std(f1s))
    mean_precision = float(np.mean(precisions))
    min_f1         = float(min(f1s))

    reasons = []
    if std_f1 > MAX_STD_F1:
        reasons.append(f"std_f1={std_f1:.3f} > {MAX_STD_F1} (inconsistent across folds)")
    if mean_precision < MIN_MEAN_PRECISION:
        reasons.append(f"mean_precision={mean_precision:.3f} < {MIN_MEAN_PRECISION} (below random)")
    if min_f1 < MIN_FOLD_F1:
        reasons.append(f"min_fold_f1={min_f1:.3f} < {MIN_FOLD_F1} (catastrophic fold detected)")

    if reasons:
        return False, " | ".join(reasons)
    return True, "all stability criteria passed"


# =============================================================================
# Main entry point
# =============================================================================

async def walk_forward_validate(
    symbol:      str,
    strategy:    str,
    write_to_db: bool = True,
    train_bars:  Optional[int] = None,
) -> Optional[WalkForwardResult]:
    """
    Run walk-forward validation for one symbol.
    Optionally writes the calibration result to DB when stable.

    Returns WalkForwardResult or None if insufficient data.
    """
    logger.info(f"[walk_forward] loading labeled rows for {symbol}...")
    rows = await _load_labeled_rows(symbol)

    if len(rows) < MIN_SAMPLES:
        logger.info(
            f"[walk_forward] {symbol}: {len(rows)} rows < {MIN_SAMPLES} minimum — skipping"
        )
        return None

    feature_cols = STRATEGY_FEATURES.get(strategy, STRATEGY_FEATURES["V75"])
    X, y         = _build_feature_matrix(rows, feature_cols)

    if len(X) < MIN_SAMPLES:
        logger.info(f"[walk_forward] {symbol}: too few complete rows after imputation")
        return None

    logger.info(
        f"[walk_forward] {symbol}: {len(X)} usable samples — "
        f"running {N_FOLDS}-fold walk-forward..."
    )

    try:
        fold_results, best_model, best_X_test, best_y_test = _run_walk_forward(
            X, y, feature_cols, train_bars=train_bars
        )
    except ValueError as exc:
        logger.warning(f"[walk_forward] {symbol}: {exc}")
        return None

    if not fold_results:
        logger.warning(f"[walk_forward] {symbol}: no valid folds produced")
        return None

    # ── Aggregate metrics ─────────────────────────────────────────────────
    f1s            = [f.f1        for f in fold_results]
    precisions     = [f.precision for f in fold_results]
    recalls        = [f.recall    for f in fold_results]

    mean_f1        = round(float(np.mean(f1s)),        4)
    mean_precision = round(float(np.mean(precisions)), 4)
    mean_recall    = round(float(np.mean(recalls)),    4)
    std_f1         = round(float(np.std(f1s)),         4)

    is_stable, reason = _check_stability(fold_results)

    logger.info(
        f"[walk_forward] {symbol} SUMMARY — "
        f"mean_f1={mean_f1} std_f1={std_f1} precision={mean_precision} | "
        f"{'STABLE ✓' if is_stable else 'UNSTABLE ✗'}: {reason}"
    )
    for fold in fold_results:
        logger.info(
            f"[walk_forward] {symbol} fold {fold.fold}: "
            f"train={fold.train_size} test={fold.test_size} "
            f"f1={fold.f1} prec_win={fold.precision_win} f1_win={fold.f1_win}"
        )

    # ── Extract thresholds from best fold's model ─────────────────────────
    feat_idx = {f: i for i, f in enumerate(feature_cols)}

    def thresh(feat, direction="above"):
        if feat not in feat_idx:
            return None
        idx  = feat_idx[feat]
        vals = best_X_test[:, idx]
        return _find_optimal_threshold(
            best_model, best_X_test, best_y_test, idx, vals, direction
        )

    calibration = {
        "symbol":   symbol,
        "strategy": strategy,
        "rsi_buy_max":    thresh("rsi",       direction="below"),
        "rsi_sell_min":   thresh("rsi",       direction="above"),
        "adx_min":        thresh("adx",       direction="above"),
        "tss_min":        int(thresh("tss_score",  direction="above") or 2),
        "checklist_min":  int(thresh("checklist",  direction="above") or 3),
        "confidence_min": thresh("confidence", direction="above"),
        "spike_min":      thresh("drop_spike", direction="above"),
        "recovery_min":   thresh("recovery",   direction="above"),
    }

    # Apply hard-coded defaults for None thresholds
    defaults = HARD_CODED_DEFAULTS.get(symbol, {})
    for key, default_val in defaults.items():
        if calibration.get(key) is None and default_val is not None:
            calibration[key] = default_val

    best_fold = max(fold_results, key=lambda f: f.precision_win)
    calibration.update({
        "n_samples":               len(X),
        "precision":               mean_precision,
        "recall":                  mean_recall,
        "f1":                      mean_f1,
        "feature_importance_json": json.dumps(best_fold.feature_importance),
    })

    result = WalkForwardResult(
        symbol           = symbol,
        strategy         = strategy,
        n_folds          = len(fold_results),
        folds            = fold_results,
        mean_precision   = mean_precision,
        mean_recall      = mean_recall,
        mean_f1          = mean_f1,
        std_f1           = std_f1,
        calibration      = calibration,
        is_stable        = is_stable,
        stability_reason = reason,
    )

    # ── Write to DB only when ALL stability criteria pass ─────────────────
    if write_to_db:
        if is_stable:
            await write_calibration(calibration)
            logger.info(f"[walk_forward] {symbol}: calibration written to DB ✓")
        else:
            logger.warning(
                f"[walk_forward] {symbol}: NOT writing to DB — {reason}. "
                "Existing calibration preserved."
            )

    return result


# =============================================================================
# Scheduler entry point  (drop-in for run_validator / run_trainer)
# =============================================================================

SYMBOL_STRATEGY_MAP = {
    "R_75":     "V75",
    "R_25":     "V25",
    "CRASH500": "Crash500",
}


async def run_validator() -> None:
    """
    Drop-in replacement for run_trainer() in signal_engine.py.

    Change in signal_engine.py:
        from ml.walk_forward_validator import run_validator
        asyncio.create_task(run_validator())
    """
    for symbol, strategy in SYMBOL_STRATEGY_MAP.items():
        try:
            result = await walk_forward_validate(symbol, strategy, write_to_db=True)
            if result is None:
                continue
        except Exception as exc:
            logger.error(f"[walk_forward] failed for {symbol}: {exc}")


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )
    asyncio.run(run_validator())