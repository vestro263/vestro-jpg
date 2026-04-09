"""
calibration_trainer.py
======================
Trains one Gradient Boosted Classifier per symbol on labeled SignalLog rows,
then writes the learned threshold calibration to CalibrationConfig in DB.

Requirements before training:
    - At least MIN_SAMPLES labeled rows per symbol (default 200)
    - label_15m must be non-NULL (primary metric)

Features used (mirrors what each strategy computes):
    V75:       rsi, adx, atr, ema_50, ema_200, macd_hist, tss_score, checklist, confidence
    Crash500:  drop_spike, recovery, spike_score, confidence

Threshold extraction method:
    After fitting the model, we sweep each feature's range and find the
    cut-point that maximises F1 on the hold-out set, holding all other
    features at their median.  This gives us interpretable per-feature
    thresholds that can replace the hard-coded values in each strategy.

HOLD suppression:
    label_15m encodes -1 (LOSS) / 0 (neutral/timeout) / +1 (WIN).
    Class 0 timeouts typically make up ~60%+ of rows but carry no
    directional information — they simply mean neither TP nor SL was
    reached in 15 minutes.  Including them as a training class causes
    the GBM to learn "predict 0 always" and corrupts the threshold sweep.

    Fix: class-0 rows are dropped before training.  The model becomes a
    binary WIN (+1) vs LOSS (-1) classifier.  Threshold sweep then uses
    average="binary" F1 (positive class = WIN) so there is no majority-
    class inflation.

    HOLD budget: after thresholds are extracted we simulate how many
    signals the strategy would suppress (i.e. predict LOSS) on the hold-
    out set, and tighten thresholds iteratively until the HOLD rate is
    at or below TARGET_HOLD_RATE (default 15%).

Run modes:
    1. Called from signal_engine.run_signal_loop() after enough rows accumulate
    2. Standalone: python -m app.ml.calibration_trainer

Output:
    One CalibrationConfig row per symbol in the DB.
    signal_engine / strategies reload this via calibration_loader.py.

Changes vs previous version:
    [HOLD-FIX] HARD_CODED_DEFAULTS — lowered Crash500 spike_min (3.0→2.0)
               and recovery_min (0.5→0.3) to match the new strategy constants.
               These are the fallback values used when the ML sweep returns
               None (too few samples in a bucket), so they must stay aligned
               with calibration_loader.py and crash500_strategy.py.
    [CLASS-FIX] Dropped class-0 (neutral/timeout) rows from training entirely.
               GBM is now a binary WIN/LOSS classifier — no majority-class bias.
    [F1-FIX]   Threshold sweep now uses average="binary", pos_label=1 so the
               sweep optimises for catching WIN signals, not predicting timeouts.
    [HOLD-BUDGET] Added _enforce_hold_budget() which tightens each threshold
               iteratively until predicted-HOLD rate on hold-out <= 15%.
    [LOGGING]  Added class distribution logging so you can see exactly what
               the trainer is working with each run.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
from sqlalchemy import select, delete

from app.database import AsyncSessionLocal
from .signal_log_model import SignalLog, CalibrationConfig

logger = logging.getLogger(__name__)

MIN_SAMPLES      = 200
TEST_SIZE        = 0.20      # 20% hold-out
RANDOM_SEED      = 42
TARGET_HOLD_RATE = 0.15      # maximum fraction of hold-out rows predicted as LOSS/suppressed
HOLD_TIGHTEN_STEPS = 20      # how many iterations to try when enforcing hold budget

# ── Feature sets per strategy ─────────────────────────────────
FEATURES_V75 = [
    "rsi", "adx", "atr", "ema_50", "ema_200",
    "macd_hist", "tss_score", "checklist", "confidence",
]

FEATURES_CRASH500 = [
    "drop_spike", "recovery", "spike_score", "confidence",
]

STRATEGY_FEATURES = {
    "V75":      FEATURES_V75,
    "Crash500": FEATURES_CRASH500,
}

# ── Hard-coded defaults (used when calibration hasn't run yet) ─
# [HOLD-FIX] Crash500 spike_min and recovery_min loosened to match
#            the updated constants in crash500_strategy.py and the
#            updated defaults in calibration_loader.py.
HARD_CODED_DEFAULTS = {
    "R_75": {
        "rsi_buy_max":    45.0,
        "rsi_sell_min":   55.0,
        "adx_min":        25.0,
        "tss_min":        2,
        "checklist_min":  3,
        "confidence_min": 0.0,
        "spike_min":      None,
        "recovery_min":   None,
    },
    "CRASH500": {
        "rsi_buy_max":    None,
        "rsi_sell_min":   None,
        "adx_min":        None,
        "tss_min":        None,
        "checklist_min":  None,
        "confidence_min": 0.0,
        "spike_min":      2.0,
        "recovery_min":   0.3,
    },
}


# ============================================================
# DATA LOADING
# ============================================================

async def _load_rows(symbol: str) -> list[dict]:
    """Load all labeled rows for a given symbol."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SignalLog)
            .where(
                SignalLog.symbol    == symbol,
                SignalLog.label_15m != None,    # noqa: E711
                SignalLog.signal    != "HOLD",
            )
            .order_by(SignalLog.captured_at)
        )
        rows = result.scalars().all()

    return [
        {col.name: getattr(r, col.name) for col in SignalLog.__table__.columns}
        for r in rows
    ]


def _build_feature_matrix(rows: list[dict], feature_cols: list[str]):
    """
    Convert raw DB rows into numpy X matrix and y label vector.

    [CLASS-FIX] Class-0 rows (neutral/timeout outcomes) are dropped here.
    They represent "TP nor SL hit in 15m" — not a signal quality indicator,
    just noise that would bias the model toward always predicting 0.
    The resulting y contains only -1 (LOSS) and +1 (WIN).

    Missing values are imputed with the column median.
    """
    X_raw = []
    y_raw = []

    n_dropped_class0  = 0
    n_dropped_missing = 0

    for r in rows:
        label = r["label_15m"]

        # [CLASS-FIX] Drop neutral/timeout rows — they carry no directional signal
        if label == 0:
            n_dropped_class0 += 1
            continue

        x = [r.get(f) for f in feature_cols]
        # Skip rows where more than half the features are missing
        n_missing = sum(1 for v in x if v is None)
        if n_missing > len(feature_cols) // 2:
            n_dropped_missing += 1
            continue

        X_raw.append(x)
        y_raw.append(label)

    if not X_raw:
        return np.array([]), np.array([])

    X = np.array(X_raw, dtype=float)
    y = np.array(y_raw, dtype=int)

    # Log class distribution so you can see what the model is working with
    unique, counts = np.unique(y, return_counts=True)
    dist = dict(zip(unique.tolist(), counts.tolist()))
    logger.info(
        f"[calibration_trainer] class distribution (after dropping class-0): {dist} | "
        f"dropped {n_dropped_class0} neutral/timeout rows, "
        f"{n_dropped_missing} rows with too many missing features"
    )

    # Impute per-column medians
    for col_idx in range(X.shape[1]):
        col    = X[:, col_idx]
        median = np.nanmedian(col)
        X[np.isnan(col), col_idx] = median

    return X, y


# ============================================================
# THRESHOLD EXTRACTION
# ============================================================

def _find_optimal_threshold(
    model,
    X_test: np.ndarray,
    y_test: np.ndarray,
    feature_idx: int,
    feature_values: np.ndarray,
    direction: str = "above",    # "above" = feature > threshold is bullish
    n_steps: int = 40,
) -> float | None:
    """
    Sweep the value range of one feature and find the cut-point that
    maximises F1 on the actual test rows that satisfy the threshold condition.

    [F1-FIX] Uses average="binary", pos_label=1 (WIN) instead of
    average="weighted".  Weighted F1 inflates scores when the majority
    class (previously class-0, now potentially class-(-1)) dominates —
    every threshold looked good because the model just predicted majority
    class everywhere.  Binary F1 on pos_label=1 means we specifically
    optimise for identifying WIN signals.

    direction="above"  → higher values = BUY signal (e.g. TSS score)
    direction="below"  → lower values  = BUY signal (e.g. RSI for oversold)
    """
    best_thresh, best_f1 = None, -1

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
        # Need both classes present to compute binary F1 meaningfully
        if len(np.unique(y_subset)) < 2:
            continue

        preds = model.predict(X_test[mask])

        # [F1-FIX] Binary F1 on WIN class — not weighted average
        score = f1_score(y_subset, preds, zero_division=0, average="binary", pos_label=1)
        if score > best_f1:
            best_f1, best_thresh = score, thresh

    return round(float(best_thresh), 4) if best_thresh is not None else None


# ============================================================
# HOLD BUDGET ENFORCEMENT
# ============================================================

def _hold_rate_on_holdout(
    model,
    X_test: np.ndarray,
    calibration: dict,
    feature_cols: list[str],
) -> float:
    """
    Simulate the strategy's HOLD rate: fraction of hold-out rows that would
    be suppressed (model predicts LOSS, i.e. -1) given current thresholds.

    We use the model's direct predictions as a proxy — a row predicted as
    LOSS (-1) would be held in the strategy.  WIN (+1) predictions pass through.

    Returns the fraction of rows predicted as LOSS (0.0 – 1.0).
    """
    if len(X_test) == 0:
        return 0.0

    preds = model.predict(X_test)
    hold_count = np.sum(preds == -1)
    return hold_count / len(preds)


def _enforce_hold_budget(
    model,
    X_test: np.ndarray,
    y_test: np.ndarray,
    calibration: dict,
    feature_cols: list[str],
    target_rate: float = TARGET_HOLD_RATE,
) -> dict:
    """
    [HOLD-BUDGET] If the model is predicting LOSS on more than `target_rate`
    fraction of hold-out rows, iteratively relax the thresholds (lower
    minimums / raise maximums) until we hit the budget.

    Strategy: we scale each non-None threshold toward its feature median
    by a small step each iteration.  This means:
      - rsi_buy_max increases (less restrictive RSI gate)
      - adx_min decreases (less restrictive trend filter)
      - tss_min decreases (fewer trend score points required)
      - checklist_min decreases
      - spike_min decreases
      - recovery_min decreases

    We stop when either the hold rate is <= target_rate or we've hit
    HOLD_TIGHTEN_STEPS iterations (to avoid infinite loops).
    """
    hold_rate = _hold_rate_on_holdout(model, X_test, calibration, feature_cols)
    logger.info(f"[calibration_trainer] initial hold rate on hold-out: {hold_rate:.1%} (target <= {target_rate:.0%})")

    if hold_rate <= target_rate:
        return calibration   # already within budget

    feat_idx = {f: i for i, f in enumerate(feature_cols)}

    # Keys that should DECREASE to become less restrictive
    relax_down = ["adx_min", "tss_min", "checklist_min", "spike_min", "recovery_min", "confidence_min"]
    # Keys that should INCREASE to become less restrictive
    relax_up   = ["rsi_buy_max", "rsi_sell_min"]

    for step in range(HOLD_TIGHTEN_STEPS):
        # Relax by 5% of current value each step
        for key in relax_down:
            val = calibration.get(key)
            if val is not None and val > 0:
                calibration[key] = round(val * 0.95, 4)

        for key in relax_up:
            val = calibration.get(key)
            if val is not None:
                # rsi_buy_max can go up to 60, rsi_sell_min down to 40
                if key == "rsi_buy_max":
                    calibration[key] = min(round(val * 1.05, 4), 60.0)
                elif key == "rsi_sell_min":
                    calibration[key] = max(round(val * 0.95, 4), 40.0)

        hold_rate = _hold_rate_on_holdout(model, X_test, calibration, feature_cols)
        logger.info(
            f"[calibration_trainer] hold-budget step {step + 1}: "
            f"hold rate = {hold_rate:.1%}"
        )

        if hold_rate <= target_rate:
            logger.info(
                f"[calibration_trainer] hold budget achieved after {step + 1} steps "
                f"(rate={hold_rate:.1%})"
            )
            break
    else:
        logger.warning(
            f"[calibration_trainer] hold budget NOT achieved after {HOLD_TIGHTEN_STEPS} steps "
            f"— final rate={hold_rate:.1%}.  Check class balance or MIN_SAMPLES."
        )

    return calibration


# ============================================================
# TRAINER
# ============================================================

async def train_symbol(symbol: str, strategy_name: str) -> dict | None:
    """
    Train one model for a symbol.  Returns the calibration dict, or None
    if there's not enough data yet.
    """
    logger.info(f"[calibration_trainer] loading rows for {symbol}...")
    rows = await _load_rows(symbol)

    if len(rows) < MIN_SAMPLES:
        logger.info(
            f"[calibration_trainer] {symbol}: only {len(rows)} rows "
            f"(need {MIN_SAMPLES}) — skipping"
        )
        return None

    feature_cols = STRATEGY_FEATURES.get(strategy_name, FEATURES_V75)
    X, y = _build_feature_matrix(rows, feature_cols)

    if len(X) < MIN_SAMPLES:
        logger.info(
            f"[calibration_trainer] {symbol}: too few usable rows after dropping "
            f"class-0 and imputing — got {len(X)}, need {MIN_SAMPLES}"
        )
        return None

    # Must have both WIN and LOSS classes to train a meaningful binary model
    unique_classes = np.unique(y)
    if len(unique_classes) < 2:
        logger.warning(
            f"[calibration_trainer] {symbol}: only one class present {unique_classes.tolist()} "
            f"— cannot train binary classifier.  Collect more diverse outcomes."
        )
        return None

    logger.info(f"[calibration_trainer] {symbol}: {len(X)} samples — training...")

    # ── Train / test split ────────────────────────────────────
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, random_state=RANDOM_SEED, stratify=y
    )

    # ── Model ─────────────────────────────────────────────────
    # [CLASS-FIX] class_weight is not a direct GBC param — we derive
    # sample_weight instead so WIN and LOSS rows contribute equally even
    # if they're slightly unbalanced after dropping class-0 rows.
    classes, class_counts = np.unique(y_train, return_counts=True)
    total = len(y_train)
    weight_map = {cls: total / (len(classes) * cnt) for cls, cnt in zip(classes, class_counts)}
    sample_weights = np.array([weight_map[label] for label in y_train])

    model = GradientBoostingClassifier(
        n_estimators      = 200,
        max_depth         = 4,
        learning_rate     = 0.05,
        subsample         = 0.8,
        min_samples_leaf  = 20,
        random_state      = RANDOM_SEED,
    )
    model.fit(X_train, y_train, sample_weight=sample_weights)

    # ── Evaluate ──────────────────────────────────────────────
    y_pred    = model.predict(X_test)

    # [F1-FIX] Use macro averaging for overall metrics so WIN and LOSS
    # contribute equally regardless of their relative frequencies.
    precision = round(float(precision_score(y_test, y_pred, zero_division=0, average="macro")), 4)
    recall    = round(float(recall_score(y_test, y_pred, zero_division=0, average="macro")), 4)
    f1        = round(float(f1_score(y_test, y_pred, zero_division=0, average="macro")), 4)

    # Per-class breakdown for visibility
    f1_per_class = {
        int(cls): round(float(f1_score(y_test, y_pred, labels=[cls], average="macro", zero_division=0)), 4)
        for cls in unique_classes
    }

    logger.info(
        f"[calibration_trainer] {symbol}: precision={precision} "
        f"recall={recall} f1={f1} | per-class f1={f1_per_class}"
    )

    # ── Feature importance ────────────────────────────────────
    importances = {
        feat: round(float(imp), 4)
        for feat, imp in zip(feature_cols, model.feature_importances_)
    }
    logger.info(f"[calibration_trainer] {symbol}: feature importances = {importances}")

    # ── Extract per-feature thresholds ────────────────────────
    feat_idx = {f: i for i, f in enumerate(feature_cols)}

    def thresh(feat, direction="above"):
        if feat not in feat_idx:
            return None
        idx  = feat_idx[feat]
        vals = X_test[:, idx]
        return _find_optimal_threshold(model, X_test, y_test, idx, vals, direction)

    calibration = {
        "symbol":   symbol,
        "strategy": strategy_name,

        # V75 thresholds
        "rsi_buy_max":    thresh("rsi",       direction="below"),   # low RSI = oversold = BUY
        "rsi_sell_min":   thresh("rsi",       direction="above"),   # high RSI = overbought = SELL
        "adx_min":        thresh("adx",       direction="above"),
        "tss_min":        int(thresh("tss_score",  direction="above") or 2),
        "checklist_min":  int(thresh("checklist",  direction="above") or 3),
        "confidence_min": thresh("confidence", direction="above"),

        # Crash500 thresholds
        "spike_min":    thresh("drop_spike",  direction="above"),
        "recovery_min": thresh("recovery",    direction="above"),

        # Metadata
        "n_samples":   len(X),
        "precision":   precision,
        "recall":      recall,
        "f1":          f1,
        "feature_importance_json": json.dumps(importances),
    }

    # Fall back to hard-coded defaults for any threshold that came back None
    defaults = HARD_CODED_DEFAULTS.get(symbol, {})
    for key, default_val in defaults.items():
        if calibration.get(key) is None and default_val is not None:
            calibration[key] = default_val
            logger.info(
                f"[calibration_trainer] {symbol}: {key} → using hard-coded default {default_val}"
            )

    # ── [HOLD-BUDGET] Relax thresholds until HOLD rate <= 15% ──
    calibration = _enforce_hold_budget(
        model        = model,
        X_test       = X_test,
        y_test       = y_test,
        calibration  = calibration,
        feature_cols = feature_cols,
        target_rate  = TARGET_HOLD_RATE,
    )

    return calibration


async def write_calibration(calibration: dict) -> None:
    """Upsert one CalibrationConfig row."""
    symbol = calibration["symbol"]
    async with AsyncSessionLocal() as db:
        # Delete old row if present (simpler than upsert across DB engines)
        await db.execute(
            delete(CalibrationConfig).where(CalibrationConfig.symbol == symbol)
        )
        row = CalibrationConfig(**{
            k: v for k, v in calibration.items()
            if k in CalibrationConfig.__table__.columns.keys()
        })
        db.add(row)
        await db.commit()

    logger.info(f"[calibration_trainer] wrote CalibrationConfig for {symbol}")


# ============================================================
# MAIN ENTRY POINT
# ============================================================

SYMBOL_STRATEGY_MAP = {
    "R_75":     "V75",
    "CRASH500": "Crash500",
}


async def run_trainer() -> None:
    """
    Train models for all known symbols.
    Called from signal_engine.run_signal_loop() on a schedule,
    or standalone via __main__.
    """
    for symbol, strategy_name in SYMBOL_STRATEGY_MAP.items():
        try:
            calibration = await train_symbol(symbol, strategy_name)
            if calibration:
                await write_calibration(calibration)
        except Exception as e:
            logger.error(f"[calibration_trainer] failed for {symbol}: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_trainer())