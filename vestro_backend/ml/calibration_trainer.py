"""
calibration_trainer.py
======================
Precision-focused trainer — only WIN vs LOSS, no neutral noise.
"""

import asyncio
import json
import logging
from datetime import datetime

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
from sqlalchemy import select, delete

from app.database import AsyncSessionLocal
from .signal_log_model import SignalLog, CalibrationConfig

logger = logging.getLogger(__name__)

MIN_SAMPLES = 200
TEST_SIZE   = 0.20
RANDOM_SEED = 42

FEATURES_V75 = [
    "rsi", "adx", "atr", "ema_50", "ema_200",
    "macd_hist", "tss_score", "confidence",
]

FEATURES_CRASH500 = [
    "drop_spike", "recovery", "spike_score", "confidence",
]

STRATEGY_FEATURES = {
    "V75":      FEATURES_V75,
    "Crash500": FEATURES_CRASH500,
}

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

SYMBOL_STRATEGY_MAP = {
    "R_75":     "V75",
    "CRASH500": "Crash500",
}


# ============================================================
# DATA LOADING — WIN vs LOSS only, no NEUTRAL, no HOLD
# ============================================================

async def _load_rows(symbol: str) -> list[dict]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SignalLog)
            .where(
                SignalLog.symbol    == symbol,
                SignalLog.label_15m != None,        # noqa: E711
                SignalLog.label_15m != 0,           # ← drop NEUTRAL rows
                SignalLog.signal    != "HOLD",      # ← drop HOLD rows
            )
            .order_by(SignalLog.captured_at)
        )
        rows = result.scalars().all()

    return [
        {col.name: getattr(r, col.name) for col in SignalLog.__table__.columns}
        for r in rows
    ]


def _build_feature_matrix(rows: list[dict], feature_cols: list[str]):
    X_raw, y_raw = [], []

    for r in rows:
        x = [r.get(f) for f in feature_cols]
        n_missing = sum(1 for v in x if v is None)
        if n_missing > len(feature_cols) // 2:
            continue
        X_raw.append(x)
        y_raw.append(r["label_15m"])

    X = np.array(X_raw, dtype=float)
    y = np.array(y_raw, dtype=int)

    # Impute medians
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
    direction: str = "above",
    n_steps: int = 40,
) -> float | None:
    best_thresh, best_precision = None, -1

    thresholds = np.linspace(
        np.nanpercentile(feature_values, 5),
        np.nanpercentile(feature_values, 95),
        n_steps,
    )

    for thresh in thresholds:
        mask = feature_values >= thresh if direction == "above" else feature_values <= thresh
        if mask.sum() < 10:
            continue
        preds = model.predict(X_test[mask])
        # Optimise for PRECISION not F1
        score = precision_score(y_test[mask], preds, zero_division=0, average="weighted")
        if score > best_precision:
            best_precision, best_thresh = score, thresh

    return round(float(best_thresh), 4) if best_thresh is not None else None


# ============================================================
# TRAINER
# ============================================================

async def train_symbol(symbol: str, strategy_name: str) -> dict | None:
    logger.info(f"[calibration_trainer] loading WIN/LOSS rows for {symbol}...")
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

    # Check class balance
    wins   = int(np.sum(y == 1))
    losses = int(np.sum(y == -1))
    logger.info(f"[calibration_trainer] {symbol}: WIN={wins} LOSS={losses} total={len(X)}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, random_state=RANDOM_SEED, stratify=y
    )

    # ── Precision-focused model ───────────────────────────────
    model = GradientBoostingClassifier(
        n_estimators     = 300,      # more trees = more precision
        max_depth        = 3,        # shallower = less overfit
        learning_rate    = 0.03,     # slower learning = more precise
        subsample        = 0.7,
        min_samples_leaf = 10,       # tighter patterns
        max_features     = "sqrt",   # feature randomness reduces overfit
        random_state     = RANDOM_SEED,
    )
    model.fit(X_train, y_train)

    y_pred    = model.predict(X_test)
    precision = round(float(precision_score(y_test, y_pred, zero_division=0, average="weighted")), 4)
    recall    = round(float(recall_score(y_test, y_pred, zero_division=0, average="weighted")), 4)
    f1        = round(float(f1_score(y_test, y_pred, zero_division=0, average="weighted")), 4)

    logger.info(
        f"[calibration_trainer] {symbol}: precision={precision} recall={recall} f1={f1}"
    )

    # ── Precision gate — don't write garbage to DB ────────────
    if precision < 0.55:
        logger.warning(
            f"[calibration_trainer] {symbol}: precision={precision} below 0.55 gate "
            f"— keeping existing calibration"
        )
        return None

    importances = {
        feat: round(float(imp), 4)
        for feat, imp in zip(feature_cols, model.feature_importances_)
    }
    logger.info(f"[calibration_trainer] {symbol}: feature importances={importances}")

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

        "rsi_buy_max":    thresh("rsi",       direction="below"),
        "rsi_sell_min":   thresh("rsi",       direction="above"),
        "adx_min":        thresh("adx",       direction="above"),
        "tss_min":        int(thresh("tss_score", direction="above") or 3),
        "checklist_min":  4,
        "confidence_min": thresh("confidence", direction="above"),
        "spike_min":      thresh("drop_spike", direction="above"),
        "recovery_min":   thresh("recovery",   direction="above"),

        "n_samples":              len(X),
        "precision":              precision,
        "recall":                 recall,
        "f1":                     f1,
        "feature_importance_json": json.dumps(importances),
    }

    defaults = HARD_CODED_DEFAULTS.get(symbol, {})
    for key, default_val in defaults.items():
        if calibration.get(key) is None and default_val is not None:
            calibration[key] = default_val

    return calibration


async def write_calibration(calibration: dict) -> None:
    symbol = calibration["symbol"]
    async with AsyncSessionLocal() as db:
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


async def run_trainer() -> None:
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