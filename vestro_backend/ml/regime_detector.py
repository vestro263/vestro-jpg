"""
regime_detector.py
==================
Detects market regimes from feature vectors or raw candle data.
Used in two places:

  At TRAINING TIME (retrain.py):
      Assigns a regime label to every training row so crash/high-vol rows
      can be preserved during undersampling and so regime-stratified metrics
      can be reported.

  At SIGNAL-FIRE TIME (strategy code):
      Gates signals — e.g. suppresses BUY signals in CRASH regime, loosens
      ADX threshold in HIGH_VOL regime.  Call detect_current_regime() and
      pass the result to the strategy's filter logic.

Regimes
-------
  TREND     — directional move, moderate volatility, ADX rising
  RANGE     — low volatility, price oscillating, no clear trend
  HIGH_VOL  — elevated volatility (ATR spike), direction uncertain
  CRASH     — extreme down-move, heavy lower wicks, vol spike + negative skew

Detection methods
-----------------
  Statistical (default, no fitting required):
      Rule-based on ATR%, rolling returns skew, and Donchian channel width.
      Fast, interpretable, no training data required.
      Used at signal-fire time.

  Clustering (optional, called from retrain.py):
      KMeans on a 4-feature regime proxy vector.  Clusters are labelled by
      comparing their centroid characteristics to the statistical definitions.
      More stable across different market periods.  Requires fit() before predict().

Integration
-----------
  # Signal-fire time (no fit needed):
  from ml.regime_detector import detect_current_regime, RegimeLabel
  regime = detect_current_regime(candle_df)
  if regime == RegimeLabel.CRASH:
      return  # suppress all signals during crash

  # Training time (clustering):
  from ml.regime_detector import RegimeDetector
  detector = RegimeDetector(method="clustering")
  detector.fit(X_train, feature_cols)
  regimes = detector.predict(X_train, feature_cols)  # list of RegimeLabel strings
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

# ── Regime thresholds (tuned for M15/M30 synthetic indices) ──────────────────
ATR_PCT_HIGH_VOL  = 0.012   # ATR/price > 1.2% = elevated volatility
ATR_PCT_CRASH     = 0.025   # ATR/price > 2.5% = extreme volatility
SKEW_CRASH        = -1.5    # strongly negative return skew = crash signature
DONCHIAN_RANGE    = 0.30    # Donchian width / ATR < 0.3 = ranging market
ADX_TREND         = 25.0    # ADX > 25 = directional trend present
N_CLUSTERS        = 4       # one cluster per regime


class RegimeLabel(Enum):
    TREND    = "TREND"
    RANGE    = "RANGE"
    HIGH_VOL = "HIGH_VOL"
    CRASH    = "CRASH"
    UNKNOWN  = "UNKNOWN"


# =============================================================================
# Statistical regime detection  (no fit, works at signal-fire time)
# =============================================================================

def detect_current_regime(
    candle_df:    pd.DataFrame,
    lookback:     int = 20,
) -> RegimeLabel:
    """
    Detect the current regime from the last `lookback` bars of a candle
    feature DataFrame (as returned by feature_engineering.build_feature_df).

    Returns a RegimeLabel enum.  Fast — no model required.

    Parameters
    ----------
    candle_df : output of build_feature_df()  — must contain at minimum:
                atr_pct, log_ret_1, dist_hi_20, dist_lo_20
    lookback  : number of recent bars to use for regime classification
    """
    if candle_df.empty or len(candle_df) < lookback:
        return RegimeLabel.UNKNOWN

    recent = candle_df.iloc[-lookback:]

    atr_pct     = float(recent["atr_pct"].mean())     if "atr_pct"    in recent else 0.01
    log_rets    = recent["log_ret_1"].dropna().values  if "log_ret_1"  in recent else np.zeros(lookback)
    dist_hi     = float(recent["dist_hi_20"].mean())   if "dist_hi_20" in recent else 0.0
    dist_lo     = float(recent["dist_lo_20"].mean())   if "dist_lo_20" in recent else 0.0

    donchian_width = abs(dist_hi - dist_lo)
    skew           = float(pd.Series(log_rets).skew()) if len(log_rets) > 2 else 0.0

    # ── Decision tree ─────────────────────────────────────────────────────
    # CRASH: extreme vol + heavy negative skew
    if atr_pct > ATR_PCT_CRASH and skew < SKEW_CRASH:
        return RegimeLabel.CRASH

    # HIGH_VOL: elevated vol without directional crash signature
    if atr_pct > ATR_PCT_HIGH_VOL:
        return RegimeLabel.HIGH_VOL

    # RANGE: tight Donchian channel relative to ATR
    if donchian_width < DONCHIAN_RANGE:
        return RegimeLabel.RANGE

    # Default: TREND (directional move, moderate volatility)
    return RegimeLabel.TREND


def label_regime_statistical(row: dict, all_cols: list[str]) -> str:
    """
    Classify a single SignalLog row dict using statistical rules.
    Extracts regime-proxy features from whatever columns are available.
    Used as a fallback when clustering is not available.
    """
    atr_pct = float(row.get("atr_pct", 0.0) or 0.0)
    log_ret = float(row.get("log_ret_1", 0.0) or 0.0)
    vol_rat = float(row.get("vol_ratio_5_20", 1.0) or 1.0)
    dist_hi = float(row.get("dist_hi_20", 0.5) or 0.5)
    dist_lo = float(row.get("dist_lo_20", -0.5) or -0.5)
    donchian_width = abs(dist_hi - dist_lo)

    if atr_pct > ATR_PCT_CRASH and log_ret < -0.01:
        return RegimeLabel.CRASH.value
    if atr_pct > ATR_PCT_HIGH_VOL or vol_rat > 2.0:
        return RegimeLabel.HIGH_VOL.value
    if donchian_width < DONCHIAN_RANGE:
        return RegimeLabel.RANGE.value
    return RegimeLabel.TREND.value


# =============================================================================
# Clustering-based detector  (for training time, needs fit)
# =============================================================================

class RegimeDetector:
    """
    KMeans-based regime classifier.

    fit(X, feature_cols)     — learn 4 clusters from training data
    predict(X, feature_cols) — assign regime label to each row

    Cluster assignment to regime labels is done by comparing each cluster's
    centroid to statistical thresholds.  No manual cluster-to-label mapping
    needed.

    Usage in retrain.py:
        detector = RegimeDetector()
        detector.fit(X_train, all_cols)
        regime_labels = detector.predict(X_train, all_cols)
    """

    # Regime-proxy feature names (subset of CANDLE_FEATURE_NAMES)
    PROXY_FEATURES = ["atr_pct", "vol_ratio_5_20", "log_ret_1", "dist_hi_20"]

    def __init__(self, n_clusters: int = N_CLUSTERS, random_state: int = 42):
        self.n_clusters    = n_clusters
        self.random_state  = random_state
        self._kmeans:  Optional[KMeans]         = None
        self._scaler:  Optional[StandardScaler] = None
        self._cluster_labels: dict[int, str]    = {}

    # ------------------------------------------------------------------
    def fit(self, X: np.ndarray, feature_cols: list[str]) -> "RegimeDetector":
        """
        Fit KMeans on proxy features extracted from X.
        Then label each cluster by comparing its centroid to statistical thresholds.
        """
        Z, valid = self._extract_proxy(X, feature_cols)
        if Z is None:
            logger.warning("[regime_detector] proxy features not available — falling back to statistical mode")
            return self

        self._scaler = StandardScaler()
        Z_scaled     = self._scaler.fit_transform(Z)

        self._kmeans = KMeans(
            n_clusters  = self.n_clusters,
            random_state = self.random_state,
            n_init       = 10,
        )
        self._kmeans.fit(Z_scaled)

        # Label clusters by centroid characteristics
        centroids_orig = self._scaler.inverse_transform(self._kmeans.cluster_centers_)
        self._cluster_labels = self._label_clusters(centroids_orig)

        cluster_counts = {}
        for k in range(self.n_clusters):
            mask = self._kmeans.labels_ == k
            cluster_counts[self._cluster_labels[k]] = int(mask.sum())

        logger.info(f"[regime_detector] fitted — cluster distribution: {cluster_counts}")
        return self

    # ------------------------------------------------------------------
    def predict(self, X: np.ndarray, feature_cols: list[str]) -> list[str]:
        """
        Assign a regime label string to each row in X.

        Falls back to statistical labelling when:
        - fit() was not called (no clustering model available)
        - proxy features are missing from feature_cols
        """
        if self._kmeans is None or self._scaler is None:
            logger.info("[regime_detector] no clustering model — using statistical fallback")
            return self._statistical_predict(X, feature_cols)

        Z, valid = self._extract_proxy(X, feature_cols)
        if Z is None:
            return self._statistical_predict(X, feature_cols)

        Z_scaled     = self._scaler.transform(Z)
        cluster_ids  = self._kmeans.predict(Z_scaled)

        labels = []
        for i, cluster_id in enumerate(cluster_ids):
            labels.append(self._cluster_labels.get(int(cluster_id), RegimeLabel.UNKNOWN.value))

        return labels

    # ------------------------------------------------------------------
    def _extract_proxy(
        self,
        X:            np.ndarray,
        feature_cols: list[str],
    ) -> tuple[Optional[np.ndarray], list[int]]:
        """Extract the 4 proxy feature columns from X."""
        col_map = {f: i for i, f in enumerate(feature_cols)}
        indices = [col_map[f] for f in self.PROXY_FEATURES if f in col_map]

        if len(indices) < 2:
            return None, []

        Z = X[:, indices].copy()
        # Replace NaN with column medians
        for j in range(Z.shape[1]):
            col    = Z[:, j]
            median = np.nanmedian(col)
            Z[np.isnan(col), j] = median if not np.isnan(median) else 0.0

        return Z, indices

    # ------------------------------------------------------------------
    def _label_clusters(self, centroids: np.ndarray) -> dict[int, str]:
        """
        Assign a regime label to each cluster by inspecting the centroid
        values for atr_pct (col 0) and vol_ratio (col 1) and log_ret (col 2).
        """
        labels: dict[int, str] = {}
        for k, centroid in enumerate(centroids):
            atr_pct  = float(centroid[0]) if len(centroid) > 0 else 0.01
            vol_rat  = float(centroid[1]) if len(centroid) > 1 else 1.0
            log_ret  = float(centroid[2]) if len(centroid) > 2 else 0.0

            if atr_pct > ATR_PCT_CRASH and log_ret < -0.005:
                labels[k] = RegimeLabel.CRASH.value
            elif atr_pct > ATR_PCT_HIGH_VOL or vol_rat > 1.8:
                labels[k] = RegimeLabel.HIGH_VOL.value
            elif atr_pct < 0.006:
                labels[k] = RegimeLabel.RANGE.value
            else:
                labels[k] = RegimeLabel.TREND.value

        logger.info(f"[regime_detector] cluster → regime mapping: {labels}")
        return labels

    # ------------------------------------------------------------------
    def _statistical_predict(self, X: np.ndarray, feature_cols: list[str]) -> list[str]:
        """Row-by-row statistical fallback when clustering is unavailable."""
        col_map = {f: i for i, f in enumerate(feature_cols)}
        results = []
        for row_vec in X:
            row_dict = {f: float(row_vec[col_map[f]]) for f in col_map if f in col_map}
            results.append(label_regime_statistical(row_dict, feature_cols))
        return results

    # ------------------------------------------------------------------
    def regime_distribution(self, labels: list[str]) -> dict[str, float]:
        """Return percentage breakdown of regime labels."""
        n      = len(labels)
        unique = list({l for l in labels})
        return {
            regime: round(100 * sum(1 for l in labels if l == regime) / n, 1)
            for regime in unique
        }