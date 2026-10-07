"""
ai_validator.py must apply the saved isotonic calibrator to every
predict_proba() call (get_ml_confidence is the one place "confidence %"
is computed), and must keep loading an older, pre-calibration pickle (a
bare classifier, no {"model","calibrator"} bundle) without crashing --
just uncalibrated until the next retrain replaces it.
"""

import joblib
import pytest

from ai_validator import AIValidator
from trend_engine import TrendSignal


def _signal():
    return TrendSignal(
        symbol="TEST", rsi=55.0, ema_signal="bullish", macd_signal="bullish",
        atr=1.0, vwap_signal="above", overall_trend=0.5, current_price=100.0,
    )


class _FakeModel:
    """Stands in for the XGBClassifier -- fixed raw score, no real fitting."""
    def predict_proba(self, features):
        return [[0.4, 0.6]]  # raw score 0.6 for class 1


class _FakeCalibrator:
    def __init__(self, shift):
        self.shift = shift

    def transform(self, probs):
        return [p - self.shift for p in probs]


def _validator_with(model_day=None, calibrator_day=None):
    v = AIValidator.__new__(AIValidator)
    v.enabled = True
    v.model_day = model_day
    v.model_swing = None
    v.calibrator_day = calibrator_day
    v.calibrator_swing = None
    return v


def test_get_ml_confidence_applies_the_calibrator():
    v = _validator_with(model_day=_FakeModel(), calibrator_day=_FakeCalibrator(shift=0.15))
    conf = v.get_ml_confidence(_signal(), sentiment_score=0.0, mode="day")
    assert conf == pytest.approx(0.45)  # 0.6 raw - 0.15 shift


def test_get_ml_confidence_falls_back_to_raw_score_without_a_calibrator():
    v = _validator_with(model_day=_FakeModel(), calibrator_day=None)
    conf = v.get_ml_confidence(_signal(), sentiment_score=0.0, mode="day")
    assert conf == pytest.approx(0.6)


def test_loading_a_new_bundle_pickle_extracts_model_and_calibrator(tmp_path, monkeypatch):
    bundle_path = tmp_path / "ml_validator_model_US_day.pkl"
    joblib.dump({"model": _FakeModel(), "calibrator": _FakeCalibrator(shift=0.1)}, bundle_path)

    v = AIValidator.__new__(AIValidator)
    monkeypatch.setattr(v, "_get_model_path", lambda mode: str(bundle_path))

    model, calibrator = v._load_single_model("day")
    assert isinstance(model, _FakeModel)
    assert calibrator.shift == 0.1


def test_loading_an_old_bare_classifier_pickle_is_uncalibrated_not_broken(tmp_path, monkeypatch, caplog):
    """Pre-calibration pickles (just the classifier, no bundle) must keep
    working -- uncalibrated -- rather than crash on the day this ships."""
    old_pickle_path = tmp_path / "ml_validator_model_US_day.pkl"
    joblib.dump(_FakeModel(), old_pickle_path)

    v = AIValidator.__new__(AIValidator)
    v._in_docker = False
    v.active_market = "US"
    monkeypatch.setattr(v, "_get_model_path", lambda mode: str(old_pickle_path))

    model, calibrator = v._load_single_model("day")
    assert calibrator is None
    assert model.predict_proba([[0]])[0][1] == 0.6
