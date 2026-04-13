"""
class_balancer.py
=================
Time-series-safe class balancing for the WIN / LOSS binary classifier.

Three strategies (all safe for chronological data — no shuffle, no SMOTE):

  1. compute_sample_weights(y)
     Inverse-frequency sample weights.  Pass directly to model.fit().
     Zero leakage: weights are derived from training labels only.
     Use this as the DEFAULT inside calibration_train.py and retrain.py.

  2. chronological_undersample(X, y, target_ratio, random_state)
     Reduces the majority class by randomly removing rows from within
     EACH FOLD'S TRAINING WINDOW ONLY.  Preserves temporal ordering.
     Never touches the test window.  Use when class imbalance > 3:1.

  3. BalancedBatchGenerator
     Iterator that yields (X_batch, y_batch, weights) balanced mini-batches
     in chronological order.  Suitable for incremental/online learning where
     the full dataset can't fit in memory.  Batches never cross the
     train/test boundary.

Why this improves Precision / F1
---------------------------------
SignalLog data is typically ~60% NEUTRAL (dropped), leaving WIN/LOSS often
at a 2:1 or 3:1 imbalance.  Without balancing:
  • GBM learns to predict the majority class (often LOSS for CRASH500)
  • Precision on WIN class stays low (many false positive BUY signals)
  • F1_macro is artificially inflated by LOSS class dominance

With sample weights (strategy 1), the model treats each WIN row as worth
2–3× a LOSS row → decision boundary shifts toward more balanced precision.

Integration with calibration_train.py
----------------------------------------
Replace the sample_weights computation in train_symbol() with:

    from .class_balancer import compute_sample_weights, chronological_undersample

    # Option A: sample weights (always safe, minimal code change)
    sample_weights = compute_sample_weights(y_train)
    model.fit(X_train, y_train, sample_weight=sample_weights)

    # Option B: undersample when imbalance > 3:1
    X_train_bal, y_train_bal = chronological_undersample(X_train, y_train)
    sample_weights = compute_sample_weights(y_train_bal)
    model.fit(X_train_bal, y_train_bal, sample_weight=sample_weights)
"""

from __future__ import annotations

import logging
from typing import Generator, Optional

import numpy as np

logger = logging.getLogger(__name__)


# =============================================================================
# Strategy 1: Inverse-frequency sample weights
# =============================================================================

def compute_sample_weights(
    y: np.ndarray,
    smooth: float = 0.0,
) -> np.ndarray:
    """
    Compute inverse-frequency sample weights for class balancing.

    Parameters
    ----------
    y      : label array, e.g. np.array([-1, 1, 1, -1, -1, 1, ...])
    smooth : smoothing factor added to counts before inversion.
             Higher values reduce the strength of balancing.
             0.0 = full inverse frequency (hard balance).
             0.1 = slight smoothing for noisy datasets.

    Returns
    -------
    np.ndarray of shape (n,) with per-sample weights.
    Weights are normalised so they sum to n (preserves effective learning rate).
    """
    classes, counts = np.unique(y, return_counts=True)
    n               = len(y)

    # weight_c = n / (n_classes × count_c)  — standard sklearn formula
    weight_map: dict[int, float] = {}
    for cls, cnt in zip(classes, counts):
        weight_map[int(cls)] = float(n) / (len(classes) * (cnt + smooth * n))

    weights = np.array([weight_map[int(lbl)] for lbl in y], dtype=float)

    # Normalise to sum to n so the effective learning rate is unchanged
    weights = weights * (n / weights.sum())

    unique, w_counts = np.unique(y, return_counts=True)
    avg_weights = {int(cls): round(float(weights[y == cls].mean()), 3)
                  for cls in unique}
    logger.info(
        f"[class_balancer] sample weights — class distribution: "
        f"{dict(zip(unique.tolist(), w_counts.tolist()))} | "
        f"mean weights: {avg_weights}"
    )

    return weights


# =============================================================================
# Strategy 2: Chronological undersampling
# =============================================================================

def chronological_undersample(
    X:            np.ndarray,
    y:            np.ndarray,
    target_ratio: float = 1.5,
    random_state: int   = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Reduce the majority class to at most target_ratio × minority class size.
    Rows are removed randomly but the RELATIVE CHRONOLOGICAL ORDER of
    surviving rows is preserved.

    Parameters
    ----------
    X            : feature matrix (rows already in chronological order)
    y            : labels array aligned with X
    target_ratio : max allowed ratio of majority to minority class.
                   1.5 means majority ≤ 1.5 × minority count.
    random_state : random seed for reproducibility

    Returns
    -------
    (X_resampled, y_resampled) — same dtype, order preserved.

    Notes
    -----
    • Works on the training window only.  Never call on test data.
    • Only undersamples if actual ratio > target_ratio.
    • Falls back to original arrays if already balanced enough.
    """
    rng     = np.random.default_rng(random_state)
    classes, counts = np.unique(y, return_counts=True)

    if len(classes) < 2:
        return X, y

    minority_cls   = classes[np.argmin(counts)]
    majority_cls   = classes[np.argmax(counts)]
    minority_count = int(counts.min())
    majority_count = int(counts.max())

    actual_ratio = majority_count / minority_count
    if actual_ratio <= target_ratio:
        logger.info(
            f"[class_balancer] no undersampling needed "
            f"(ratio={actual_ratio:.2f} ≤ target={target_ratio})"
        )
        return X, y

    target_majority = int(minority_count * target_ratio)
    majority_indices = np.where(y == majority_cls)[0]
    keep_count       = min(target_majority, len(majority_indices))

    # Random sample WITHOUT sort — then restore sort to preserve chronology
    keep_majority = np.sort(rng.choice(majority_indices, size=keep_count, replace=False))
    minority_indices = np.where(y == minority_cls)[0]
    all_keep         = np.sort(np.concatenate([minority_indices, keep_majority]))

    X_res = X[all_keep]
    y_res = y[all_keep]

    new_unique, new_counts = np.unique(y_res, return_counts=True)
    logger.info(
        f"[class_balancer] undersampled: {majority_count} → {keep_count} majority rows | "
        f"new distribution: {dict(zip(new_unique.tolist(), new_counts.tolist()))}"
    )

    return X_res, y_res


# =============================================================================
# Strategy 3: Balanced batch generator (chronological)
# =============================================================================

class BalancedBatchGenerator:
    """
    Yields chronological mini-batches with balanced class representation.

    Each batch contains roughly equal numbers of WIN (+1) and LOSS (-1) rows,
    drawn from a sliding window to preserve local temporal context.

    Usage:
        gen = BalancedBatchGenerator(X_train, y_train, batch_size=64)
        for X_batch, y_batch, weights in gen:
            model.fit(X_batch, y_batch, sample_weight=weights)

    Notes
    -----
    • Batches slide forward in time — no future data enters a batch.
    • If one class runs out in the current window, the batch is skipped.
    • This is designed for incremental retraining in retrain.py, not for
      initial training (use compute_sample_weights for that).
    """

    def __init__(
        self,
        X:            np.ndarray,
        y:            np.ndarray,
        batch_size:   int = 64,
        overlap:      float = 0.5,
        random_state: int  = 42,
    ):
        """
        Parameters
        ----------
        X           : feature matrix in chronological order
        y           : labels aligned with X
        batch_size  : rows per batch (half from each class when balanced)
        overlap     : fraction of the batch window to slide forward each step
        random_state: for reproducibility
        """
        self.X            = X
        self.y            = y
        self.batch_size   = batch_size
        self.overlap      = overlap
        self.rng          = np.random.default_rng(random_state)
        self._n_batches: Optional[int] = None

    def __iter__(self) -> Generator[tuple[np.ndarray, np.ndarray, np.ndarray], None, None]:
        n         = len(self.X)
        step      = max(1, int(self.batch_size * (1 - self.overlap)))
        per_class = self.batch_size // 2
        batches   = 0

        for start in range(0, n - self.batch_size, step):
            end     = min(start + self.batch_size * 3, n)   # look-ahead window
            X_win   = self.X[start:end]
            y_win   = self.y[start:end]

            win_idx  = np.where(y_win ==  1)[0]
            loss_idx = np.where(y_win == -1)[0]

            if len(win_idx) < per_class // 2 or len(loss_idx) < per_class // 2:
                continue   # not enough of each class in this window

            n_win  = min(per_class, len(win_idx))
            n_loss = min(per_class, len(loss_idx))

            sel_win  = np.sort(self.rng.choice(win_idx,  n_win,  replace=False))
            sel_loss = np.sort(self.rng.choice(loss_idx, n_loss, replace=False))
            sel      = np.sort(np.concatenate([sel_win, sel_loss]))

            X_batch = X_win[sel]
            y_batch = y_win[sel]
            weights = compute_sample_weights(y_batch)

            batches += 1
            yield X_batch, y_batch, weights

        self._n_batches = batches
        logger.info(f"[class_balancer] BalancedBatchGenerator yielded {batches} batches")

    def __len__(self) -> int:
        n    = len(self.X)
        step = max(1, int(self.batch_size * (1 - self.overlap)))
        return max(0, (n - self.batch_size) // step)


# =============================================================================
# Utility: report class balance for a label array
# =============================================================================

def report_balance(y: np.ndarray, label: str = "") -> dict:
    """
    Print and return class distribution stats.
    Call before and after balancing to verify the effect.
    """
    unique, counts = np.unique(y, return_counts=True)
    total  = len(y)
    dist   = {int(cls): int(cnt) for cls, cnt in zip(unique, counts)}
    pcts   = {int(cls): round(100 * cnt / total, 1) for cls, cnt in zip(unique, counts)}
    ratios = {}

    if len(counts) >= 2:
        majority = counts.max()
        minority = counts.min()
        ratios["majority_to_minority"] = round(majority / minority, 2)

    report = {"counts": dist, "pct": pcts, **ratios}
    prefix = f"[class_balancer] {label} " if label else "[class_balancer] "
    logger.info(f"{prefix}balance report: {report}")
    return report