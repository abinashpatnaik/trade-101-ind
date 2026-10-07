"""
ContinuousLearning used to save its EOD retrain to a bare
ml_validator_model_{MARKET}.pkl -- a file ai_validator.py never loads (it
only loads the _day/_swing suffixed files). Every EOD retrain silently had
zero effect on live trading. It must save to the suffixed swing path.
"""

import os

import pytest

from continuous_learning import ContinuousLearning
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
