"""Investment-horizon features computed from daily closes."""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = ["mom_1m", "mom_3m", "mom_6m", "mom_12m", "vol_3m", "dist_sma200", "drawdown_1y", "rsi_14"]


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def feature_frame(close: pd.Series) -> pd.DataFrame:
    """One row per day for a single asset."""
    c = close.dropna()
    rets = c.pct_change()
    return pd.DataFrame({
        "mom_1m": c.pct_change(21),
        "mom_3m": c.pct_change(63),
        "mom_6m": c.pct_change(126),
        # 12-1 momentum: skip the latest month, which tends to mean-revert
        "mom_12m": c.shift(21) / c.shift(252) - 1,
        "vol_3m": rets.rolling(63).std() * np.sqrt(252),
        "dist_sma200": c / c.rolling(200).mean() - 1,
        "drawdown_1y": c / c.rolling(252).max() - 1,
        "rsi_14": _rsi(c),
    })


def latest_features(closes: pd.DataFrame) -> pd.DataFrame:
    rows = {}
    for t in closes.columns:
        f = feature_frame(closes[t]).dropna()
        if not f.empty:
            rows[t] = f.iloc[-1]
    return pd.DataFrame(rows).T[FEATURES] if rows else pd.DataFrame(columns=FEATURES)


def momentum_scores(feats: pd.DataFrame) -> pd.Series:
    """Risk-adjusted momentum, ranked across the candidates, mapped to [-1, 1]."""
    if feats.empty:
        return pd.Series(dtype=float)
    raw = (feats["mom_3m"] + feats["mom_6m"] + feats["mom_12m"]) / 3 / feats["vol_3m"].clip(lower=0.05)
    if len(raw) == 1:
        return np.tanh(raw)
    z = (raw - raw.mean()) / (raw.std(ddof=0) or 1.0)
    return np.tanh(z)


def above_trend(close: pd.Series) -> bool:
    c = close.dropna()
    if len(c) < 200:
        return True
    return bool(c.iloc[-1] >= c.rolling(200).mean().iloc[-1])
