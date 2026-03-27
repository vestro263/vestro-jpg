"""
ml/train.py — run locally when you have labelled outcome data.

Usage:
    python ml/train.py --data labelled_firms.csv

Expected CSV columns:
    headcount_delta_90d, headcount_signal_count, funding_log_usd,
    days_since_funding, sentiment_mean, sentiment_count,
    sentiment_positive_ratio, label
    (label: 1 = firm rose in value, 0 = fell / stagnant)

The script:
    1. Trains a GradientBoostingClassifier
    2. Prints CV AUC + classification report
    3. Saves model.pkl and shap_explainer.pkl next to this file
"""
import argparse
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report
import shap

from app.pipeline.features import FEATURE_NAMES

HERE           = Path(__file__).parent
MODEL_PATH     = HERE / "model.pkl"
EXPLAINER_PATH = HERE / "shap_explainer.pkl"


def train(data_path: str):
    df = pd.read_csv(data_path)
    X  = df[FEATURE_NAMES].fillna(0)
    y  = df["label"]

    print(f"\n▸ {len(df)} samples  |  positive rate: {y.mean():.1%}\n")

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = GradientBoostingClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.04,
        subsample=0.8,
        min_samples_leaf=5,
        random_state=42,
    )
    model.fit(X_tr, y_tr)

    cv = cross_val_score(model, X, y, cv=5, scoring="roc_auc")
    print(f"CV AUC  {cv.mean():.3f} ± {cv.std():.3f}")
    print(classification_report(y_te, model.predict(X_te)))

    print("Feature importances:")
    for name, imp in sorted(
        zip(FEATURE_NAMES, model.feature_importances_), key=lambda x: -x[1]
    ):
        bar = "█" * int(imp * 40)
        print(f"  {name:35s} {bar}  {imp:.4f}")

    joblib.dump(model, MODEL_PATH)
    print(f"\n✓ model saved → {MODEL_PATH}")

    explainer = shap.TreeExplainer(model)
    joblib.dump(explainer, EXPLAINER_PATH)
    print(f"✓ SHAP explainer saved → {EXPLAINER_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="Path to labelled CSV")
    train(parser.parse_args().data)