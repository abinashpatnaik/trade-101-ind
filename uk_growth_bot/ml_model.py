"""XGBoost model: P(asset beats the universe median over the next ~3 months).

Validated walk-forward with an embargo gap. If out-of-sample AUC is below
settings.ml_min_auc the model is kept but given ZERO weight — an unproven
model must not move real money.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Dict, Optional

import joblib
import numpy as np
import pandas as pd

from .config import settings
from .features import FEATURES, feature_frame

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(settings.data_dir, "ml_model.joblib")
META_PATH = os.path.join(settings.data_dir, "ml_model.json")


@dataclass
class ModelReport:
    trained_at: str
    samples: int
    test_samples: int
    auc: float
    accuracy: float
    hit_rate_top: float   # how often the model's top-ranked half actually beat the median
    active: bool


def build_dataset(closes: pd.DataFrame, horizon: int) -> pd.DataFrame:
    frames = []
    fwd = closes.shift(-horizon) / closes - 1
    for t in closes.columns:
        f = feature_frame(closes[t])
        f["fwd"] = fwd[t]
        f["ticker"] = t
        frames.append(f)
    df = pd.concat(frames).dropna(subset=FEATURES)
    # Weekly sampling: daily rows of a 3-month label are ~60x duplicated info.
    df = df[df.index.dayofweek == 4]
    df = df.dropna(subset=["fwd"]).copy()
    med = df.groupby(level=0)["fwd"].transform("median")
    df["label"] = (df["fwd"] > med).astype(int)
    return df.sort_index()


def _new_model():
    from xgboost import XGBClassifier
    return XGBClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8,
                         colsample_bytree=0.8, min_child_weight=5, eval_metric="logloss",
                         verbosity=0)


def train(closes: pd.DataFrame) -> Optional[ModelReport]:
    from sklearn.metrics import accuracy_score, roc_auc_score

    horizon = settings.ml_horizon_days
    df = build_dataset(closes, horizon)
    dates = df.index.unique()
    if len(dates) < 104:
        logger.warning("Not enough history to train (%d weekly dates)", len(dates))
        return None

    split = dates[int(len(dates) * 0.75)]
    embargo = split - pd.Timedelta(days=int(horizon * 1.5))
    train_df, test_df = df[df.index < embargo], df[df.index >= split]
    model = _new_model()
    model.fit(train_df[FEATURES], train_df["label"])
    p = model.predict_proba(test_df[FEATURES])[:, 1]
    y = test_df["label"].values
    auc = float(roc_auc_score(y, p)) if len(set(y)) > 1 else 0.5
    acc = float(accuracy_score(y, p >= 0.5))
    top = p >= np.median(p)
    hit_top = float(y[top].mean()) if top.any() else 0.0

    final = _new_model()
    final.fit(df[FEATURES], df["label"])
    os.makedirs(settings.data_dir, exist_ok=True)
    joblib.dump(final, MODEL_PATH)
    report = ModelReport(datetime.utcnow().isoformat(timespec="seconds"), len(df), len(test_df),
                         round(auc, 4), round(acc, 4), round(hit_top, 4), auc >= settings.ml_min_auc)
    with open(META_PATH, "w") as fh:
        json.dump(asdict(report), fh, indent=2)
    logger.info("ML retrained: %s", report)
    return report


def load_report() -> Optional[ModelReport]:
    try:
        with open(META_PATH) as fh:
            return ModelReport(**json.load(fh))
    except (OSError, ValueError, TypeError):
        return None


def predict_scores(feats: pd.DataFrame) -> Dict[str, float]:
    """{ticker: score in [-1, 1]}; empty when no validated model exists."""
    report = load_report()
    if feats.empty or not report or not report.active or not os.path.exists(MODEL_PATH):
        return {}
    model = joblib.load(MODEL_PATH)
    p = model.predict_proba(feats[FEATURES].astype(float))[:, 1]
    return {t: float(2 * pi - 1) for t, pi in zip(feats.index, p)}
