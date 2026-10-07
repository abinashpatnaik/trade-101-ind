"""
The continuous-learning label override used to treat ANY trade exiting via
TRAILING_STOP as an automatic win regardless of its actual pnl -- a trailing
stop can still close at a small loss (gap, slippage). label_from_pnl() is
the extracted, correct version: outcome comes from pnl's sign alone.
"""

import math

import pytest

from ml_trainer import label_from_pnl


def test_positive_pnl_is_a_win():
    assert label_from_pnl(12.5) == 1


def test_negative_pnl_is_a_loss():
    assert label_from_pnl(-3.2) == 0


def test_zero_pnl_is_a_loss_not_a_win():
    assert label_from_pnl(0.0) == 0


def test_trailing_stop_with_a_loss_is_labeled_a_loss():
    """The bug: exit_reason used to force this to a win regardless of pnl."""
    # label_from_pnl only ever sees pnl -- exit_reason plays no part,
    # which is the fix. A trailing-stop exit that still lost money must
    # label as a loss.
    assert label_from_pnl(-0.75) == 0


def test_none_pnl_is_skipped_not_guessed():
    assert label_from_pnl(None) is None


def test_nan_pnl_is_skipped_not_guessed():
    assert label_from_pnl(float("nan")) is None
