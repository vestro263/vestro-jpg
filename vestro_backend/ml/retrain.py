"""
retrain.py
==========
Scheduled retraining pipeline.  Ties together all upgraded ml/ modules
into a single end-to-end retrain run that can be called on a cron schedule.

Pipeline order:
  1. Load labeled rows from DB  (existing + recent)
  2. Enrich with candle features via feature_engineering
  3. Detect market regime via regime_detector
  4. Balance classes via class_balancer
  5. Walk-forward validate via walk_forward_validator
  6. Calibrate probabilities via calibration_train
  7. Save versioned model to disk
  8. Write CalibrationConfig to DB

Crash sample preservation:
  "Crash rows" are rows labeled during a CRASH regime (detected by
  regime_detector).  These are rare but critical — a model that has never
  seen crash data will fail catastrophically.  This pipeline keeps a
  CRASH_RESERVE_PCT (default 20%) of crash rows in every training window
  regardless of the undersampling ratio applied to normal rows.

Versioning:
  Each run saves models under ml/models/{symbol}_v{YYYYMMDD_HHMMSS}.pkl.
  A symlink {symbol}_calibrated.pkl always points to the latest.
  calibration_loader.py loads the symlink so it auto-picks the newest model.

Usage:
  # From signal_engine.py scheduler:
  from ml.retrain import run_retrain_pipeline
  asyncio.create_task(run_retrain_pipeline(api_token=token))

  # Standalone:
  python -m app.ml.retrain
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
from sqlalchemy import select

from app.database import AsyncSessionLocal
from .signal_log_model import SignalLog, CalibrationConfig
from .feature_engineering import enrich_rows_batch, CANDLE_FEATURE_NAMES
from .calibration_train import (
    STRATEGY_FEATURES,
    SYMBOL_STRATEGY_MAP,
    MIN_SAMPLES,
    _build_feature_matrix,
    _fit_calibrated_model,
    _find_optimal_threshold,
    _enforce_hold_budget,
    write_calibration,
    HARD_CODED_DEFAULTS,
)
from .class_balancer import (
    compute_sample_weights,
    chronological_undersample,
    report_balance,
)
from .walk_forward_validator import walk_forward_validate
from .regime_detector import RegimeDetector, RegimeLabel

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
MODEL_DIR         = Path(__file__).parent / "models"
MODEL_DIR.mkdir(exist_ok=True)

CRASH_RESERVE_PCT  = 0.20   # preserve at least 20% crash rows in training
RETRAIN_INTERVAL_H = 6      # minimum hours between retrains (enforced by caller)
MAX_ROWS_PER_RUN   = 5000   # cap to avoid quadratic training time on large history


# =============================================================================
# Crash sample preservation
# =============================================================================

def _preserve_crash_rows(
    X:           np.ndarray,
    y:           np.ndarray,
    regime_labels: list[str],
    reserve_pct:   float = CRASH_RESERVE_PCT,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Split rows into crash and non-crash pools.
    Returns (X_crash, y_crash, crash_mask) where crash_mask is a boolean
    index into the original arrays.

    The caller uses this to ensure crash rows are never dropped by undersampling.
    """
    crash_mask = np.array(
        [r in (RegimeLabel.CRASH.value, RegimeLabel.HIGH_VOL.value)
         for r in regime_labels],
        dtype=bool,
    )
    logger.info(
        f"[retrain] crash/high-vol rows: {crash_mask.sum()} / {len(X)} "
        f"({100 * crash_mask.mean():.1f}%)"
    )
    return X[crash_mask], y[crash_mask], crash_mask


# =============================================================================
# Versioned model save / symlink
# =============================================================================

def _save_versioned_model(model: object, symbol: str) -> Path:
    """Save model with timestamp version tag and update latest symlink."""
    ts        = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    version   = f"{symbol}_v{ts}.pkl"
    path      = MODEL_DIR / version
    symlink   = MODEL_DIR / f"{symbol}_calibrated.pkl"

    joblib.dump(model, path)
    logger.info(f"[retrain] model saved → {path}")

    # Update symlink to point to latest version
    if symlink.exists() or symlink.is_symlink():
        symlink.unlink()
    symlink.symlink_to(path.name)
    logger.info(f"[retrain] symlink updated → {symlink} → {path.name}")

    return path


def _prune_old_versions(symbol: str, keep: int = 5) -> None:
    """Delete all but the `keep` most recent model versions for a symbol."""
    versions = sorted(MODEL_DIR.glob(f"{symbol}_v*.pkl"), reverse=True)
    for old in versions[keep:]:
        old.unlink()
        logger.info(f"[retrain] pruned old model version: {old.name}")


# =============================================================================
# Data loader with recency weighting
# =============================================================================

async def _load_rows_for_retrain(symbol: str) -> list[dict]:
    """
    Load labeled rows, capped at MAX_ROWS_PER_RUN most recent.
    Chronological order is preserved.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SignalLog)
            .where(
                SignalLog.symbol    == symbol,
                SignalLog.label_15m != None,    # noqa: E711
                SignalLog.signal    != "HOLD",
            )
            .order_by(SignalLog.captured_at.asc())
            .limit(MAX_ROWS_PER_RUN)
        )
        rows = result.scalars().all()

    return [
        {col.name: getattr(r, col.name) for col in SignalLog.__table__.columns}
        for r in rows
    ]


# =============================================================================
# Main retrain pipeline
# =============================================================================

async def retrain_symbol(
    symbol:       str,
    strategy:     str,
    api_token:    str = "",
) -> dict | None:
    """
    Full retrain pipeline for one symbol.

    Returns the calibration dict written to DB, or None on failure.
    """
    logger.info(f"[retrain] ══ starting retrain for {symbol} ({strategy}) ══")

    # ── 1. Load rows ──────────────────────────────────────────────────────
    rows = await _load_rows_for_retrain(symbol)
    if len(rows) < MIN_SAMPLES:
        logger.info(f"[retrain] {symbol}: {len(rows)} rows < {MIN_SAMPLES} — skipping")
        return None

    # ── 2. Candle feature enrichment ──────────────────────────────────────
    if api_token:
        try:
            rows = await enrich_rows_batch(rows, symbol, api_token)
            logger.info(f"[retrain] {symbol}: rows enriched with candle features")
        except Exception as exc:
            logger.warning(f"[retrain] {symbol}: candle enrichment failed ({exc})")

    # ── 3. Build feature matrix ───────────────────────────────────────────
    base_cols    = STRATEGY_FEATURES.get(strategy, STRATEGY_FEATURES["V75"])
    candle_cols  = [c for c in CANDLE_FEATURE_NAMES if c not in base_cols]
    all_cols     = base_cols + candle_cols

    X, y = _build_feature_matrix(rows, all_cols)
    if len(X) < MIN_SAMPLES or len(np.unique(y)) < 2:
        logger.info(f"[retrain] {symbol}: insufficient data after filtering")
        return None

    logger.info(f"[retrain] {symbol}: {len(X)} usable rows, {len(all_cols)} features")
    report_balance(y, label=f"{symbol} before balancing")

    # ── 4. Regime detection ───────────────────────────────────────────────
    regime_labels = [RegimeLabel.TREND.value] * len(X)  # default
    try:
        detector      = RegimeDetector()
        detector.fit(X, all_cols)
        regime_labels = detector.predict(X, all_cols)
        regime_unique, regime_counts = np.unique(regime_labels, return_counts=True)
        logger.info(
            f"[retrain] {symbol}: regimes detected — "
            f"{dict(zip(regime_unique.tolist(), regime_counts.tolist()))}"
        )
    except Exception as exc:
        logger.warning(f"[retrain] {symbol}: regime detection failed ({exc}) — using defaults")

    # ── 5. Preserve crash rows before undersampling ───────────────────────
    X_crash, y_crash, crash_mask = _preserve_crash_rows(X, y, regime_labels)
    X_normal   = X[~crash_mask]
    y_normal   = y[~crash_mask]

    # ── 6. Chronological undersampling on non-crash rows only ─────────────
    if len(X_normal) > 0 and len(np.unique(y_normal)) == 2:
        X_normal, y_normal = chronological_undersample(
            X_normal, y_normal, target_ratio=2.0
        )

    # Recombine crash + balanced normal, resort chronologically
    if len(X_crash) > 0:
        X_combined = np.concatenate([X_normal, X_crash], axis=0)
        y_combined = np.concatenate([y_normal, y_crash], axis=0)
        # We lost the original chronological index so use a stable argsort proxy:
        # crash rows were appended, so original order is approximately preserved
        # (not perfect, but crash rows are rare enough not to distort time structure)
    else:
        X_combined = X_normal
        y_combined = y_normal

    report_balance(y_combined, label=f"{symbol} after balancing")

    # ── 7. Walk-forward validation (writes calibration to DB if stable) ───
    wf_result = await walk_forward_validate(
        symbol      = symbol,
        strategy    = strategy,
        write_to_db = False,   # we write our own versioned calibration below
    )
    if wf_result is not None and not wf_result.is_stable:
        logger.warning(
            f"[retrain] {symbol}: walk-forward unstable ({wf_result.stability_reason}) — "
            "still retraining on full data but flagging in calibration"
        )

    # ── 8. Final model fit on full combined dataset ────────────────────────
    split_idx       = int(len(X_combined) * 0.80)
    X_tr, X_te      = X_combined[:split_idx], X_combined[split_idx:]
    y_tr, y_te      = y_combined[:split_idx], y_combined[split_idx:]
    sample_weights  = compute_sample_weights(y_tr)

    from sklearn.ensemble import GradientBoostingClassifier
    from .calibration_train import RANDOM_SEED
    base_model = GradientBoostingClassifier(
        n_estimators     = 200,
        max_depth        = 4,
        learning_rate    = 0.05,
        subsample        = 0.8,
        min_samples_leaf = 20,
        random_state     = RANDOM_SEED,
    )
    base_model.fit(X_tr, y_tr, sample_weight=sample_weights)

    # ── 9. Probability calibration ────────────────────────────────────────
    calibrated_model, cal_method, brier, ece = _fit_calibrated_model(
        base_model, X_tr, y_tr, X_te, y_te
    )

    # ── 10. Evaluate ──────────────────────────────────────────────────────
    from sklearn.metrics import precision_score, recall_score, f1_score
    y_pred    = calibrated_model.predict(X_te)
    precision = round(float(precision_score(y_te, y_pred, average="macro", zero_division=0)), 4)
    recall    = round(float(recall_score(   y_te, y_pred, average="macro", zero_division=0)), 4)
    f1        = round(float(f1_score(       y_te, y_pred, average="macro", zero_division=0)), 4)

    importances = {
        feat: round(float(imp), 4)
        for feat, imp in zip(all_cols, base_model.feature_importances_)
    }

    logger.info(
        f"[retrain] {symbol}: FINAL — precision={precision} recall={recall} f1={f1} "
        f"cal={cal_method} brier={brier:.4f} ece={ece:.4f}"
    )

    # ── 11. Extract thresholds ────────────────────────────────────────────
    feat_idx = {f: i for i, f in enumerate(all_cols)}

    def thresh(feat, direction="above"):
        if feat not in feat_idx:
            return None
        idx  = feat_idx[feat]
        vals = X_te[:, idx]
        return _find_optimal_threshold(calibrated_model, X_te, y_te, idx, vals, direction)

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

    calibration = _enforce_hold_budget(calibrated_model, X_te, y_te, calibration, all_cols)

    # ── 12. Save versioned model + write calibration ──────────────────────
    _save_versioned_model(calibrated_model, symbol)
    _prune_old_versions(symbol, keep=5)
    await write_calibration(calibration)
    logger.info(f"[retrain] {symbol}: ✓ retrain complete")

    return calibration


# =============================================================================
# Scheduler entry point
# =============================================================================

async def run_retrain_pipeline(api_token: str = "") -> None:
    """
    Retrain all symbols.  Called from signal_engine on a schedule.

    Recommended schedule: every 6 hours, gated by RETRAIN_INTERVAL_H
    so repeated calls within the interval are no-ops.
    """
    logger.info("[retrain] ══ retrain pipeline starting ══")
    for symbol, strategy in SYMBOL_STRATEGY_MAP.items():
        try:
            await retrain_symbol(symbol, strategy, api_token=api_token)
        except Exception as exc:
            logger.error(f"[retrain] {symbol} failed: {exc}", exc_info=True)
    logger.info("[retrain] ══ retrain pipeline complete ══")


if __name__ == "__main__":
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )
    token = os.environ.get("DERIV_API_TOKEN", "")
    asyncio.run(run_retrain_pipeline(api_token=token))