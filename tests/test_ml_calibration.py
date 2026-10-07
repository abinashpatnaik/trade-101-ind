"""
Probability calibration (fix #4): raw XGBoost predict_proba was used
directly as "confidence %" with no check that e.g. 0.60 actually meant a
60% historical win rate. fit_calibrator() fits an isotonic regression on
POOLED OUT-OF-FOLD predictions (never the final model's own training data,
which would just calibrate the model to agree with itself).
"""

import numpy as np
import pytest

from ml_trainer import fit_calibrator, calibration_report


def test_calibrator_corrects_a_systematically_overconfident_model():
    """Raw scores cluster near 0.9 but only ~50% actually won -- a classic
    overconfident-tree-ensemble pattern. The calibrator should pull the
    calibrated value down toward the true rate."""
    rng = np.random.default_rng(0)
    n = 2000
    raw_probs = rng.uniform(0.85, 0.95, n)
    # True win rate is only ~50%, independent of the (overconfident) score.
    labels = (rng.random(n) < 0.50).astype(int)

    calibrator = fit_calibrator(raw_probs, labels)
    calibrated = calibrator.transform(raw_probs)

    assert calibrated.mean() < raw_probs.mean()
    assert abs(calibrated.mean() - 0.50) < 0.05


def test_calibrator_is_monotonic_in_the_raw_score():
    """Isotonic regression must preserve rank order -- a higher raw score
    should never map to a lower calibrated one."""
    rng = np.random.default_rng(1)
    n = 2000
    true_prob = rng.uniform(0, 1, n)
    labels = (rng.random(n) < true_prob).astype(int)
    raw_probs = np.clip(true_prob + rng.normal(0, 0.05, n), 0, 1)

    calibrator = fit_calibrator(raw_probs, labels)
    order = np.argsort(raw_probs)
    calibrated_sorted = calibrator.transform(raw_probs)[order]
    diffs = np.diff(calibrated_sorted)
    assert (diffs >= -1e-9).all()


def test_calibration_report_improves_brier_score_for_overconfident_model():
    rng = np.random.default_rng(2)
    n = 2000
    raw_probs = rng.uniform(0.85, 0.95, n)
    labels = (rng.random(n) < 0.50).astype(int)

    calibrator = fit_calibrator(raw_probs, labels)
    report = calibration_report(raw_probs, labels, calibrator)

    assert report["n"] == n
    assert report["brier_calibrated"] < report["brier_raw"]


def test_calibration_report_is_a_no_op_for_an_already_calibrated_model():
    """When the raw score already IS the true probability, calibration
    shouldn't make things meaningfully worse."""
    rng = np.random.default_rng(3)
    n = 3000
    true_prob = rng.uniform(0.1, 0.9, n)
    labels = (rng.random(n) < true_prob).astype(int)

    calibrator = fit_calibrator(true_prob, labels)
    report = calibration_report(true_prob, labels, calibrator)

    # Isotonic regression on already-good scores should track closely;
    # allow a small margin for finite-sample noise.
    assert report["brier_calibrated"] <= report["brier_raw"] + 0.01
