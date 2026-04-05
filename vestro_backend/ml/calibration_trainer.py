"""
calibration_trainer.py
======================
Trains one Gradient Boosted Classifier per symbol on labeled SignalLog rows,
then writes the learned threshold calibration to CalibrationConfig in DB.

Requirements before training:
    - At least MIN_SAMPLES labeled rows per symbol (default 500)
    - label_15m must be non-NULL (primary metric)

Features used (mirrors what each strategy computes):
    V75:       rsi, adx, atr, ema_50, ema_200, macd_hist, tss_score, checklist, confidence
    Crash500:  drop_spike, recovery, spike_score, confidence

Threshold extraction method:
    After fitting the model, we sweep each feature's range and find the
    cut-point that maximises F1 on the hold-out set, holding all other
    features at their median.  This gives us interpretable per-feature
    thresholds that can replace the hard-coded values in each strategy.

Run modes:
    1. Called from signal_engine.run_signal_loop() after enough rows accumulate
    2. Standalone: python -m app.ml.calibration_trainer

Output:
    One CalibrationConfig row per symbol in the DB.
    signal_engine / strategies reload this via calibration_loader.py.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sqlalchemy import select, delete

from app.database import AsyncSessionLocal
from .signal_log_model import SignalLog, CalibrationConfig

logger = logging.getLogger(__name__)

MIN_SAMPLES = 500       # minimum labeled rows before we train
TEST_SIZE   = 0.20      # 20% hold-out
RANDOM_SEED = 42

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
HARD_CODED_DEFAULTS = {
    "R_75": {
        "rsi_buy_max":    45.0,
        "rsi_sell_min":   55.0,
        "adx_min":        25.0,
        "tss_min":        3,
        "checklist_min":  4,
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
        "spike_min":      3.0,
        "recovery_min":   0.5,
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
    Missing values are imputed with the column median.
    """
    import numpy as np
    X_raw = []
    y_raw = []

    for r in rows:
        x = [r.get(f) for f in feature_cols]
        # Skip rows where more than half the features are missing
        n_missing = sum(1 for v in x if v is None)
        if n_missing > len(feature_cols) // 2:
            continue
        X_raw.append(x)
        y_raw.append(r["label_15m"])   # primary metric

    X = np.array(X_raw, dtype=float)
    y = np.array(y_raw, dtype=int)

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
    Sweep the value range of one feature, holding all others at median.
    Return the threshold that maximises F1 on y_test.

    direction="above"  → higher values = BUY signal (e.g. TSS score)
    direction="below"  → lower values  = BUY signal (e.g. RSI for oversold)
    """
    medians  = np.nanmedian(X_test, axis=0)
    f1_scores = []
    thresholds = np.linspace(
        np.nanpercentile(feature_values, 5),
        np.nanpercentile(feature_values, 95),
        n_steps,
    )

    for thresh in thresholds:
        X_probe = np.tile(medians, (len(X_test), 1))
        # Apply threshold: set feature above/below threshold
        probe_val = thresh + 1 if direction == "above" else thresh - 1
        X_probe[:, feature_idx] = probe_val
        try:
            preds = model.predict(X_probe)
            score = f1_score(y_test, preds, zero_division=0, average="weighted")
            f1_scores.append((thresh, score))
        except Exception:
            continue

    if not f1_scores:
        return None

    best_thresh, _ = max(f1_scores, key=lambda x: x[1])
    return round(float(best_thresh), 4)


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
        logger.info(f"[calibration_trainer] {symbol}: too few complete rows after imputation")
        return None

    logger.info(f"[calibration_trainer] {symbol}: {len(X)} samples — training...")

    # ── Train / test split ────────────────────────────────────
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, random_state=RANDOM_SEED, stratify=y
    )

    # ── Model ─────────────────────────────────────────────────
    model = GradientBoostingClassifier(
        n_estimators      = 200,
        max_depth         = 4,
        learning_rate     = 0.05,
        subsample         = 0.8,
        min_samples_leaf  = 20,
        random_state      = RANDOM_SEED,
    )
    model.fit(X_train, y_train)

    # ── Evaluate ──────────────────────────────────────────────
    y_pred    = model.predict(X_test)
    precision = round(float(precision_score(y_test, y_pred, zero_division=0, average="weighted")), 4)
    recall    = round(float(recall_score(y_test, y_pred, zero_division=0, average="weighted")), 4)
    f1        = round(float(f1_score(y_test, y_pred, zero_division=0, average="weighted")), 4)

    logger.info(
        f"[calibration_trainer] {symbol}: precision={precision} "
        f"recall={recall} f1={f1}"
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
        "tss_min":        int(thresh("tss_score",  direction="above") or 3),
        "checklist_min":  int(thresh("checklist",  direction="above") or 4),
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

    # Fall back hard-coded defaults for any threshold that came back None
    defaults = HARD_CODED_DEFAULTS.get(symbol, {})
    for key, default_val in defaults.items():
        if calibration.get(key) is None and default_val is not None:
            calibration[key] = default_val
            logger.info(
                f"[calibration_trainer] {symbol}: {key} → using hard-coded default {default_val}"
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