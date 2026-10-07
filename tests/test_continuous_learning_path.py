"""
ContinuousLearning used to save its EOD retrain to a bare
ml_validator_model_{MARKET}.pkl -- a file ai_validator.py never loads (it
only loads the _day/_swing suffixed files). Every EOD retrain silently had
zero effect on live trading. It must save to the suffixed swing path.

Separately (and independently of the path bug): the EOD retrain had NEVER
actually succeeded even before that -- log_daily_features() logs
TrendSignal's string fields ('bullish'/'bearish'/'neutral', 'above'/'below')
straight to CSV, and XGBoost refuses non-numeric dtypes. Confirmed live:
trainer_US.log shows this exact crash every trading day from 2026-09-28
through 2026-10-06 straight, always at the NEAR_CLOSE EOD-retrain trigger.
encode_categorical_features() is the fix.
"""

import os

import pandas as pd
import pytest
import xgboost as xgb

from continuous_learning import ContinuousLearning, encode_categorical_features
from ai_validator import AIValidator


@pytest.fixture()
def cl(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MARKET", "US")
    monkeypatch.chdir(tmp_path)
    return ContinuousLearning()


def test_model_path_is_suffixed_for_swing(cl):
    assert cl.model_path.endswith("ml_validator_model_US_swing.pkl")


def test_model_path_matches_what_ai_validator_actually_loads(cl, monkeypatch, tmp_path):
    """The fix: the two paths must agree, or a retrain is a no-op again."""
    monkeypatch.setenv("TRADING_MARKET", "US")
    monkeypatch.delenv("TRADES_CSV_PATH", raising=False)
    v = AIValidator.__new__(AIValidator)
    v.active_market = "US"
    v._in_docker = False
    loaded_path = v._get_model_path("swing")
    assert os.path.basename(cl.model_path) == os.path.basename(loaded_path)


# --- encode_categorical_features: the separate, pre-existing fit() crash ---


def test_converts_pandas_stringdtype_columns():
    """The actual bug: pandas 2.x/3.x's CSV reader defaults string columns
    to StringDtype, not plain `object` -- a naive `dtype == object` check
    (an earlier, wrong version of this fix) misses it entirely."""
    df = pd.DataFrame({
        "macd_signal": pd.array(["bullish", "bearish", "neutral"], dtype="string"),
        "ema_signal": pd.array(["bullish", "bearish", "neutral"], dtype="string"),
        "vwap_signal": pd.array(["above", "below", "above"], dtype="string"),
    })
    out = encode_categorical_features(df)
    assert out["macd_signal"].tolist() == [1, -1, 0]
    assert out["ema_signal"].tolist() == [1, -1, 0]
    assert out["vwap_signal"].tolist() == [1, -1, 1]
    assert pd.api.types.is_numeric_dtype(out["macd_signal"])


def test_converts_plain_object_dtype_columns_too():
    df = pd.DataFrame({
        "macd_signal": pd.Series(["bullish", "bearish"], dtype=object),
        "vwap_signal": pd.Series(["above", "below"], dtype=object),
    })
    out = encode_categorical_features(df)
    assert out["macd_signal"].tolist() == [1, -1]
    assert out["vwap_signal"].tolist() == [1, -1]


def test_already_numeric_columns_pass_through_unchanged():
    """Idempotent: safe to call even once future rows are logged numeric."""
    df = pd.DataFrame({"macd_signal": [1.0, -1.0, 0.0]})
    out = encode_categorical_features(df)
    assert out["macd_signal"].tolist() == [1.0, -1.0, 0.0]


def test_missing_columns_are_left_alone():
    df = pd.DataFrame({"rsi": [55.0, 60.0]})
    out = encode_categorical_features(df)
    assert list(out.columns) == ["rsi"]


def test_xgboost_fit_succeeds_after_encoding_where_it_crashed_before():
    """End-to-end reproduction of the exact live crash, using the same
    columns and dtype logged_daily_features() actually produces."""
    df = pd.DataFrame({
        "rsi": [44.63, 44.29, 60.1, 38.2],
        "macd_signal": pd.array(["bearish", "bullish", "bullish", "bearish"], dtype="string"),
        "ema_signal": pd.array(["bearish", "bearish", "bullish", "neutral"], dtype="string"),
        "vwap_signal": pd.array(["above", "above", "below", "below"], dtype="string"),
        "sentiment_score": [-0.18, 0.18, 0.0, 0.3],
    })
    y = pd.Series([1, 1, 0, 0])

    X = encode_categorical_features(df)
    clf = xgb.XGBClassifier(n_estimators=10, max_depth=2)
    clf.fit(X, y)  # must not raise
