"""
walk_forward_validator.py
=========================
Honest time-series validation for the Vestro ML calibration model.

Replaces the random train_test_split in calibration_trainer.py with
an expanding-window walk-forward approach — the industry standard for
financial ML models.

Usage:
    # Run standalone
    python -m app.ml.walk_forward_validator

    # Or call from calibration_trainer.py instead of train_test_split:
    from .walk_forward_validator import walk_forward_validate
    results = await walk_forward_validate(symbol="R_75", strategy="V75")

How it works:
    Round 1:  Train rows 0–60%   → Test rows 60–70%
    Round 2:  Train rows 0–70%   → Test rows 70–80%
    Round 3:  Train rows 0–80%   → Test rows 80–90%
    Round 4:  Train rows 0–90%   → Test rows 90–100%

    Model NEVER sees future data during training.
    Final score = average across all folds.

Output:
    WalkForwardResult dataclass with per-fold and aggregate metrics,
    plus a ready-to-write CalibrationConfig dict.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import f1_score, precision_score, recall_score
from sqlalchemy import select

from app.database import AsyncSessionLocal
from .signal_log_model import SignalLog, CalibrationConfig
from .calibration_trainer import (
    FEATURES_V75,
    FEATURES_CRASH500,
    STRATEGY_FEATURES,
    HARD_CODED_DEFAULTS,
    MIN_SAMPLES,
    RANDOM_SEED,
    _build_feature_matrix,
    _find_optimal_threshold,
    write_calibration,
)

logger = logging.getLogger(__name__)

# ── Walk-forward config ───────────────────────────────────────
N_FOLDS       = 4      # number of test windows
MIN_TRAIN_PCT = 0.50   # first training window = 50% of data
PURGE_GAP     = 10     # rows to drop between train/test (avoid leakage)


# ============================================================
# RESULT DATACLASS
# ============================================================

@dataclass
class FoldResult:
    fold:        int
    train_size:  int
    test_size:   int
    precision:   float
    recall:      float
    f1:          float
    feature_importance: dict = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    symbol:         str
    strategy:       str
    n_folds:        int
    folds:          list[FoldResult]

    # Aggregate metrics (mean across folds)
    mean_precision: float
    mean_recall:    float
    mean_f1:        float
    std_f1:         float        # consistency measure — lower is better

    # Best fold model's thresholds (highest F1 fold)
    calibration:    dict

    # Stability flag
    is_stable:      bool         # True if std_f1 < 0.10


# ============================================================
# DATA LOADER
# ============================================================

async def _load_labeled_rows(symbol: str) -> list[dict]:
    """
    Load all labeled BUY/SELL rows for a symbol, ordered by captured_at.
    Time ordering is critical — do NOT shuffle.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SignalLog)
            .where(
                SignalLog.symbol    == symbol,
                SignalLog.label_15m != None,    # noqa: E711
                SignalLog.signal    != "HOLD",
            )
            .order_by(SignalLog.captured_at.asc())   # ← must be ascending
        )
        rows = result.scalars().all()

    return [
        {col.name: getattr(r, col.name) for col in SignalLog.__table__.columns}
        for r in rows
    ]


# ============================================================
# WALK-FORWARD ENGINE
# ============================================================

def _run_walk_forward(
    X: np.ndarray,
    y: np.ndarray,
    feature_cols: list[str],
    n_folds: int = N_FOLDS,
    min_train_pct: float = MIN_TRAIN_PCT,
    purge_gap: int = PURGE_GAP,
) -> tuple[list[FoldResult], GradientBoostingClassifier, np.ndarray, np.ndarray]:
    """
    Run walk-forward validation.
    Returns fold results + the best fold's model and test data.
    """
    n = len(X)
    fold_size = int(n * (1 - min_train_pct) / n_folds)

    if fold_size < 20:
        raise ValueError(
            f"Not enough data for {n_folds} folds. "
            f"Need at least {n_folds * 20 + int(n * min_train_pct)} rows, got {n}."
        )

    fold_results = []
    best_f1      = -1
    best_model   = None
    best_X_test  = None
    best_y_test  = None

    for fold_idx in range(n_folds):
        # ── Expanding train window ────────────────────────────
        train_end  = int(n * min_train_pct) + fold_idx * fold_size
        test_start = train_end + purge_gap          # purge gap prevents leakage
        test_end   = min(test_start + fold_size, n)

        if test_start >= n or test_end <= test_start:
            logger.warning(f"[walk_forward] fold {fold_idx+1} skipped — insufficient data")
            continue

        X_train = X[:train_end]
        y_train = y[:train_end]
        X_test  = X[test_start:test_end]
        y_test  = y[test_start:test_end]

        # Skip folds with only one class in test set
        if len(np.unique(y_test)) < 2:
            logger.warning(f"[walk_forward] fold {fold_idx+1} skipped — single class in test")
            continue

        logger.info(
            f"[walk_forward] fold {fold_idx+1}/{n_folds} — "
            f"train={len(X_train)} test={len(X_test)}"
        )

        # ── Train ─────────────────────────────────────────────
        model = GradientBoostingClassifier(
            n_estimators     = 200,
            max_depth        = 4,
            learning_rate    = 0.05,
            subsample        = 0.8,
            min_samples_leaf = 20,
            random_state     = RANDOM_SEED,
        )
        model.fit(X_train, y_train)

        # ── Evaluate ──────────────────────────────────────────
        y_pred    = model.predict(X_test)
        precision = round(float(precision_score(y_test, y_pred, zero_division=0, average="weighted")), 4)
        recall    = round(float(recall_score(y_test, y_pred, zero_division=0, average="weighted")), 4)
        f1        = round(float(f1_score(y_test, y_pred, zero_division=0, average="weighted")), 4)

        importances = {
            feat: round(float(imp), 4)
            for feat, imp in zip(feature_cols, model.feature_importances_)
        }

        fold_result = FoldResult(
            fold        = fold_idx + 1,
            train_size  = len(X_train),
            test_size   = len(X_test),
            precision   = precision,
            recall      = recall,
            f1          = f1,
            feature_importance = importances,
        )
        fold_results.append(fold_result)

        logger.info(
            f"[walk_forward] fold {fold_idx+1} — "
            f"precision={precision} recall={recall} f1={f1}"
        )

        # Track best fold for threshold extraction
        if f1 > best_f1:
            best_f1     = f1
            best_model  = model
            best_X_test = X_test
            best_y_test = y_test

    return fold_results, best_model, best_X_test, best_y_test


# ============================================================
# THRESHOLD EXTRACTION (reuses calibration_trainer logic)
# ============================================================

def _extract_thresholds(
    model,
    X_test: np.ndarray,
    y_test: np.ndarray,
    feature_cols: list[str],
    symbol: str,
    strategy: str,
) -> dict:
    """Extract per-feature thresholds from the best fold's model."""
    feat_idx = {f: i for i, f in enumerate(feature_cols)}

    def thresh(feat, direction="above"):
        if feat not in feat_idx:
            return None
        idx  = feat_idx[feat]
        vals = X_test[:, idx]
        return _find_optimal_threshold(model, X_test, y_test, idx, vals, direction)

    calibration = {
        "symbol":   symbol,
        "strategy": strategy,

        # V75 thresholds
        "rsi_buy_max":    thresh("rsi",        direction="below"),
        "rsi_sell_min":   thresh("rsi",        direction="above"),
        "adx_min":        thresh("adx",        direction="above"),
        "tss_min":        int(thresh("tss_score",  direction="above") or 3),
        "checklist_min":  int(thresh("checklist",  direction="above") or 4),
        "confidence_min": thresh("confidence", direction="above"),

        # Crash500 thresholds
        "spike_min":    thresh("drop_spike", direction="above"),
        "recovery_min": thresh("recovery",   direction="above"),
    }

    # Fall back to hard-coded defaults for any None threshold
    defaults = HARD_CODED_DEFAULTS.get(symbol, {})
    for key, default_val in defaults.items():
        if calibration.get(key) is None and default_val is not None:
            calibration[key] = default_val
            logger.info(f"[walk_forward] {symbol}: {key} → hard-coded default {default_val}")

    return calibration


# ============================================================
# MAIN ENTRY POINT
# ============================================================

async def walk_forward_validate(
    symbol: str,
    strategy: str,
    write_to_db: bool = True,
) -> WalkForwardResult | None:
    """
    Run walk-forward validation for one symbol.
    Optionally writes the calibration result to DB.

    Returns WalkForwardResult or None if insufficient data.
    """
    logger.info(f"[walk_forward] loading labeled rows for {symbol}...")
    rows = await _load_labeled_rows(symbol)

    if len(rows) < MIN_SAMPLES:
        logger.info(
            f"[walk_forward] {symbol}: only {len(rows)} labeled rows "
            f"(need {MIN_SAMPLES}) — skipping"
        )
        return None

    feature_cols = STRATEGY_FEATURES.get(strategy, FEATURES_V75)
    X, y = _build_feature_matrix(rows, feature_cols)

    if len(X) < MIN_SAMPLES:
        logger.info(f"[walk_forward] {symbol}: too few complete rows after imputation")
        return None

    logger.info(f"[walk_forward] {symbol}: {len(X)} samples — running {N_FOLDS}-fold walk-forward...")

    try:
        fold_results, best_model, best_X_test, best_y_test = _run_walk_forward(
            X, y, feature_cols
        )
    except ValueError as e:
        logger.warning(f"[walk_forward] {symbol}: {e}")
        return None

    if not fold_results:
        logger.warning(f"[walk_forward] {symbol}: no valid folds produced")
        return None

    # ── Aggregate metrics ─────────────────────────────────────
    f1s        = [f.f1        for f in fold_results]
    precisions = [f.precision for f in fold_results]
    recalls    = [f.recall    for f in fold_results]

    mean_f1        = round(float(np.mean(f1s)),        4)
    mean_precision = round(float(np.mean(precisions)), 4)
    mean_recall    = round(float(np.mean(recalls)),    4)
    std_f1         = round(float(np.std(f1s)),         4)
    is_stable      = std_f1 < 0.10

    logger.info(
        f"[walk_forward] {symbol} SUMMARY — "
        f"mean_f1={mean_f1} std_f1={std_f1} "
        f"({'STABLE' if is_stable else 'UNSTABLE'})"
    )

    # ── Extract thresholds from best fold ─────────────────────
    calibration = _extract_thresholds(
        best_model, best_X_test, best_y_test,
        feature_cols, symbol, strategy,
    )

    # Add model quality metadata
    best_fold = max(fold_results, key=lambda f: f.f1)
    calibration.update({
        "n_samples":  len(X),
        "precision":  mean_precision,
        "recall":     mean_recall,
        "f1":         mean_f1,
        "feature_importance_json": json.dumps(best_fold.feature_importance),
    })

    result = WalkForwardResult(
        symbol         = symbol,
        strategy       = strategy,
        n_folds        = len(fold_results),
        folds          = fold_results,
        mean_precision = mean_precision,
        mean_recall    = mean_recall,
        mean_f1        = mean_f1,
        std_f1         = std_f1,
        calibration    = calibration,
        is_stable      = is_stable,
    )

    # ── Write to DB ───────────────────────────────────────────
    if write_to_db and is_stable:
        await write_calibration(calibration)
        logger.info(f"[walk_forward] {symbol}: calibration written to DB ✓")
    elif write_to_db and not is_stable:
        logger.warning(
            f"[walk_forward] {symbol}: model UNSTABLE (std_f1={std_f1}) — "
            f"keeping existing calibration, not writing to DB"
        )

    return result


# ============================================================
# REPLACE run_trainer in calibration_trainer.py
# ============================================================

SYMBOL_STRATEGY_MAP = {
    "R_75":     "V75",
    "CRASH500": "Crash500",
}


async def run_validator() -> None:
    """
    Drop-in replacement for run_trainer() in signal_engine.py.
    Uses walk-forward validation instead of random split.

    In signal_engine.py change:
        from ml.calibration_trainer import run_trainer
        asyncio.create_task(run_trainer(), ...)

    To:
        from ml.walk_forward_validator import run_validator
        asyncio.create_task(run_validator(), ...)
    """
    for symbol, strategy in SYMBOL_STRATEGY_MAP.items():
        try:
            result = await walk_forward_validate(symbol, strategy, write_to_db=True)
            if result:
                logger.info(
                    f"[walk_forward] {symbol}: "
                    f"folds={result.n_folds} "
                    f"mean_f1={result.mean_f1} "
                    f"std_f1={result.std_f1} "
                    f"stable={result.is_stable}"
                )
                # Log per-fold breakdown
                for fold in result.folds:
                    logger.info(
                        f"[walk_forward] {symbol} fold {fold.fold}: "
                        f"train={fold.train_size} test={fold.test_size} "
                        f"f1={fold.f1}"
                    )
        except Exception as e:
            logger.error(f"[walk_forward] failed for {symbol}: {e}")


# ============================================================
# STANDALONE
# ============================================================

if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )
    asyncio.run(run_validator())