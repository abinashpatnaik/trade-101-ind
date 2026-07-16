"""Cost-model tests: the numbers that make or break a small account."""

import pytest

from trading_costs import (
    round_trip_cost_pct,
    min_required_move_pct,
    IN_DP_CHARGE,
)


def test_in_same_day_is_cheap():
    # ₹3,000 same-day round trip: fees are a few rupees; slippage dominates.
    pct = round_trip_cost_pct(3000, overnight=False, market="IN")
    assert 0.002 < pct < 0.004  # ~0.25-0.35% incl. 0.2% slippage allowance


def test_in_overnight_dp_charge_dominates_small_positions():
    # ₹2,000 delivery round trip: DP ₹15.93 alone is ~0.8%.
    pct = round_trip_cost_pct(2000, overnight=True, market="IN")
    assert pct > 0.010  # >1% all-in — the small-account killer
    # And the fixed fee amortises away on bigger positions:
    pct_big = round_trip_cost_pct(200_000, overnight=True, market="IN")
    assert pct_big < pct / 2


def test_in_overnight_costs_more_than_intraday():
    for notional in (2000, 10_000, 100_000):
        assert round_trip_cost_pct(notional, overnight=True, market="IN") > \
               round_trip_cost_pct(notional, overnight=False, market="IN")


def test_us_costs_are_small():
    pct = round_trip_cost_pct(50, market="US")
    assert pct < 0.003  # slippage allowance dominates; fees ~0.02%


def test_fees_only_excludes_slippage():
    with_slip = round_trip_cost_pct(10_000, market="IN")
    fees_only = round_trip_cost_pct(10_000, market="IN", include_slippage=False)
    assert with_slip == pytest.approx(fees_only + 0.002)


def test_zero_notional_blocks():
    assert round_trip_cost_pct(0, market="IN") == 1.0
    assert round_trip_cost_pct(-5, market="US") == 1.0


def test_min_required_move_is_multiple_of_cost():
    cost = round_trip_cost_pct(5000, market="IN")
    assert min_required_move_pct(5000, edge_multiple=2.0, market="IN") == pytest.approx(2 * cost)


def test_dp_charge_share_of_small_position():
    # Sanity: the flat DP fee alone exceeds 0.5% on a ₹2,500 position.
    assert IN_DP_CHARGE / 2500 > 0.005
