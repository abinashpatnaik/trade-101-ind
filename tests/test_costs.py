"""Cost-model tests: the numbers that make or break a small account."""

import pytest

from trading_costs import (
    round_trip_cost_pct,
    min_required_move_pct,
    IN_DP_CHARGE,
)


def test_in_same_day_is_dominated_by_brokerage():
    """Same-day trading is NOT cheap on this account — brokerage is 0.5%/leg.

    This test previously asserted 0.2-0.4% all-in, from the resident-plan
    assumption of min(₹20, 0.03%) brokerage. Contract note CNT-26/27-67191195
    disproved it: the real round trip is ~1.22% in fees before slippage.
    """
    fees = round_trip_cost_pct(3000, overnight=False, market="IN",
                               include_slippage=False)
    assert 0.012 < fees < 0.013          # ~1.215%, brokerage + its GST is ~97%
    # Percentage cost is flat in size while the per-order cap is unset, so
    # sizing up buys no relief on the intraday schedule.
    assert round_trip_cost_pct(200_000, overnight=False, market="IN",
                               include_slippage=False) == pytest.approx(fees)


def test_in_brokerage_cap_is_the_only_size_lever(monkeypatch):
    """With a per-order cap set, and only then, size cuts cost materially."""
    import trading_costs

    monkeypatch.setattr(trading_costs, "IN_BROKERAGE_CAP", 100.0)
    small = trading_costs.round_trip_cost_pct(20_000, market="IN",
                                              include_slippage=False)
    big = trading_costs.round_trip_cost_pct(200_000, market="IN",
                                            include_slippage=False)
    assert small > 4 * big               # 1.0% -> 0.1% brokerage


def test_in_overnight_dp_charge_dominates_small_positions():
    # ₹2,000 delivery round trip: DP ₹15.93 alone is ~0.8%, on top of
    # brokerage and doubled delivery STT.
    pct = round_trip_cost_pct(2000, overnight=True, market="IN")
    assert pct > 0.010  # >1% all-in — the small-account killer
    # The FIXED part (DP) amortises away on bigger positions; the percentage
    # parts do not, so total cost falls toward the brokerage+STT floor rather
    # than halving.
    pct_big = round_trip_cost_pct(200_000, overnight=True, market="IN")
    assert pct_big < pct
    assert IN_DP_CHARGE / 2000 > 0.007   # the fixed fee is what shrinks


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


def test_model_reproduces_real_contract_note():
    """Ground truth: Zerodha contract note CNT-26/27-67191195, 28 Jul 2026.

    Two same-day round trips. Actual billed charges were ₹87.35 (brokerage
    71.88 + CGST 6.51 + SGST 6.51 + exchange txn 0.44 + STT 2.00 + SEBI 0.01)
    against a gross P&L of +₹5.08, settling to −₹82.28. This is the only
    real fee data the model is calibrated on — if it drifts, the books will
    silently diverge from NAV again, which is exactly how ~₹80/day went
    unexplained for three sessions.
    """
    diacabs = round_trip_cost_pct(3892.20, market="IN", include_slippage=False) * 3892.20
    huhtamaki = round_trip_cost_pct(3293.64, market="IN", include_slippage=False) * 3293.64
    assert diacabs + huhtamaki == pytest.approx(87.35, abs=0.10)


def test_dp_charge_share_of_small_position():
    # Sanity: the flat DP fee alone exceeds 0.5% on a ₹2,500 position.
    assert IN_DP_CHARGE / 2500 > 0.005
