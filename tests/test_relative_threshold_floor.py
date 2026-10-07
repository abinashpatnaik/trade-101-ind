"""
The per-symbol BUY threshold's floor used to be an absolute 0.50 -- which
assumes 50%+ calibrated confidence is achievable regardless of how rare the
label's positive class is. Confirmed 2026-10-07 on the real 30-symbol US
anchor universe: with a ~26% base rate, EVERY per-symbol threshold collapsed
to a flat 0.50, and raising the selection percentile from 85 to 95 didn't
help either -- both landed on the same flat plateau of the calibration
curve. The live raw-score ceiling (~0.78, from 1,719 real BUY evaluations,
never once reaching 0.80) maps to calibrated confidence well under 50%.

Switched to a floor RELATIVE to the label's own base rate:
base_rate * (1 + threshold_relative_lift). ml_trainer.py computes it once
(it has labeled ground truth); agents/vetting.py has no ground truth of its
own (only backtest-replay confidence scores) so it reuses the same value via
decision_engine.get_relative_threshold_floor() instead of an independent
absolute guess.
"""

import pytest

from ml_trainer import relative_threshold_floor
from decision_engine import DecisionEngine


def test_relative_floor_formula():
    assert relative_threshold_floor(base_rate=0.26, relative_lift=0.25) == pytest.approx(0.325)


def test_relative_floor_scales_with_base_rate():
    """A rarer label (lower base rate) gets a lower absolute floor -- the
    whole point: the floor adapts to how rare a 'win' is, instead of
    assuming every label can clear 50%."""
    low = relative_threshold_floor(base_rate=0.10, relative_lift=0.25)
    high = relative_threshold_floor(base_rate=0.40, relative_lift=0.25)
    assert low < high
    assert low == pytest.approx(0.125)
    assert high == pytest.approx(0.50)


def test_zero_lift_means_floor_equals_base_rate():
    assert relative_threshold_floor(base_rate=0.26, relative_lift=0.0) == pytest.approx(0.26)


# --- decision_engine.get_relative_threshold_floor ---------------------------


@pytest.fixture()
def engine():
    return DecisionEngine()


def test_reads_floor_from_loaded_thresholds(engine, monkeypatch):
    monkeypatch.setitem(engine.ml_thresholds["day"], "_FLOOR_", 0.325)
    assert engine.get_relative_threshold_floor(is_swing=False) == pytest.approx(0.325)


def test_falls_back_to_default_for_a_thresholds_file_without_a_floor(engine, monkeypatch):
    """An older thresholds file saved before this existed has no _FLOOR_ key
    -- must degrade to the explicit default, not KeyError."""
    monkeypatch.setitem(engine.ml_thresholds, "day", {"AAPL": 0.58})
    assert engine.get_relative_threshold_floor(is_swing=False, default=0.50) == pytest.approx(0.50)


def test_day_and_swing_floors_are_independent(engine, monkeypatch):
    monkeypatch.setitem(engine.ml_thresholds["day"], "_FLOOR_", 0.32)
    monkeypatch.setitem(engine.ml_thresholds["swing"], "_FLOOR_", 0.41)
    assert engine.get_relative_threshold_floor(is_swing=False) == pytest.approx(0.32)
    assert engine.get_relative_threshold_floor(is_swing=True) == pytest.approx(0.41)


# --- agents/vetting.py: uses the shared floor, not an independent 0.50 -----


def test_dynamic_threshold_below_old_absolute_floor_is_not_clamped_up():
    """A symbol whose backtest confidence distribution sits entirely BELOW
    the old absolute 0.50 (but above the real, lower relative floor) must
    still get a real per-symbol threshold set -- not silently clamped up
    to 0.50 as it would have been before."""
    import numpy as np
    from agents.vetting import compute_dynamic_threshold

    vals = list(np.random.default_rng(0).uniform(0.28, 0.40, 50))
    thr = compute_dynamic_threshold(vals, pctile=85.0, floor=0.30)

    assert thr < 0.50, "a real signal below the old absolute floor must not be clamped to it"
    assert thr >= 0.30


def test_dynamic_threshold_respects_the_floor_when_signal_is_weaker_still():
    import numpy as np
    from agents.vetting import compute_dynamic_threshold

    vals = list(np.random.default_rng(1).uniform(0.05, 0.15, 50))
    thr = compute_dynamic_threshold(vals, pctile=85.0, floor=0.30)
    assert thr == pytest.approx(0.30)


def test_dynamic_threshold_respects_the_ceiling():
    import numpy as np
    from agents.vetting import compute_dynamic_threshold

    vals = list(np.random.default_rng(2).uniform(0.95, 0.99, 50))
    thr = compute_dynamic_threshold(vals, pctile=85.0, floor=0.30, ceiling=0.90)
    assert thr == pytest.approx(0.90)
