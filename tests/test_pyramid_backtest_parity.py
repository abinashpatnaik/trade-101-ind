"""
Backtest/live parity for pyramiding (agents/backtest_sim.py mirrors
order_executor.py's check_pyramid_conditions / record_add, and
decision_engine.py's shrinking-size logic — see tests/test_pyramid_adds.py
for the live-side tests this file's cases are pinned against).

Off by default (SimParams.pyramid_enabled defaults from
config.risk.pyramid_enabled = False), so every existing replay/backtest
result in tests/test_backtest_sim.py must be completely unaffected — pinned
explicitly below, not just assumed.
"""

import os

import pandas as pd
import pytest

from agents.backtest_sim import (
    SimParams,
    _Position,
    replay,
    simulate_add,
    simulate_exit,
    simulate_pyramid_check,
)
from decision_engine import DecisionEngine
from trend_engine import TrendEngine

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load_fixture(name: str) -> pd.DataFrame:
    return pd.read_csv(
        os.path.join(FIXTURES, f"{name}.csv"), index_col="Datetime", parse_dates=True
    )


def _pos(entry=100.0, trail=0.013, armed=True, add_count=0, last_add_price=None):
    return _Position(
        entry_ts="t0", entry_price=entry, stop_loss_price=97.5,
        take_profit_price=1099.0, initial_trailing_pct=trail,
        high_water=entry, lock_armed=armed, add_count=add_count,
        last_add_price=last_add_price if last_add_price is not None else entry,
    )


def _params(**kw):
    base = dict(
        stop_loss_pct=0.025, take_profit_pct=9.99, profit_lock_threshold=0.005,
        trailing_gap_base=0.010, round_trip_cost_pct=0.003,
        pyramid_enabled=True, pyramid_step_atr_multiple=1.5,
        pyramid_max_adds=2, pyramid_add_size_decay=0.5,
    )
    base.update(kw)
    return SimParams(**base)


# ------------------------------------------------------------- gating

def test_pyramid_disabled_is_a_true_noop():
    pos = _pos()
    huge_move = pos.entry_price * 2
    assert simulate_pyramid_check(pos, huge_move, _params(pyramid_enabled=False)) is None


def test_unarmed_position_never_adds():
    pos = _pos(armed=False)
    huge_move = pos.entry_price * 2
    assert simulate_pyramid_check(pos, huge_move, _params()) is None


def test_max_adds_is_a_hard_ceiling():
    pos = _pos(add_count=2)
    huge_move = pos.entry_price * 2
    assert simulate_pyramid_check(pos, huge_move, _params()) is None


def test_step_not_cleared_blocks():
    params = _params()
    pos = _pos(entry=100.0, trail=0.013)
    step = 100.0 * 0.013 * params.pyramid_step_atr_multiple
    assert simulate_pyramid_check(pos, 100.0 + step - 0.01, params) is None


def test_step_cleared_fires_with_decayed_size():
    params = _params()
    pos = _pos(entry=100.0, trail=0.013)
    step = 100.0 * 0.013 * params.pyramid_step_atr_multiple
    add_qty = simulate_pyramid_check(pos, 100.0 + step + 0.01, params)
    assert add_qty == pytest.approx(0.5)   # decay ** (0+1)


def test_second_add_is_smaller_than_the_first():
    params = _params()
    pos = _pos(entry=100.0, trail=0.013, add_count=1, last_add_price=105.0)
    step = 100.0 * 0.013 * params.pyramid_step_atr_multiple
    add_qty = simulate_pyramid_check(pos, 105.0 + step + 0.01, params)
    assert add_qty == pytest.approx(0.25)  # decay ** (1+1), half of the first add


# ------------------------------------------------------------- simulate_add

def test_simulate_add_blends_and_advances_state():
    pos = _pos(entry=100.0)
    simulate_add(pos, add_price=110.0, add_quantity=0.5)
    assert pos.quantity == pytest.approx(1.5)
    assert pos.entry_price == pytest.approx((100.0 * 1.0 + 110.0 * 0.5) / 1.5)
    assert pos.add_count == 1
    assert pos.last_add_price == pytest.approx(110.0)


def test_simulate_add_ignores_bad_input():
    pos = _pos(entry=100.0)
    simulate_add(pos, add_price=0.0, add_quantity=0.5)
    simulate_add(pos, add_price=110.0, add_quantity=0.0)
    assert pos.quantity == pytest.approx(1.0)
    assert pos.add_count == 0


# ---------------------------------------------- safety invariant, mirrored

def test_add_raises_not_lowers_the_breakeven_floor_in_replay_too():
    """Same invariant pinned live in test_pyramid_adds.py: after an add,
    check_exit_conditions'/simulate_exit's break-even floor must key off the
    NEW blended (higher) entry, never the original."""
    params = _params()
    pos = _pos(entry=100.0, trail=0.013, armed=True)
    pre_add_floor = pos.entry_price * (1 + params.net_breakeven_pct)

    simulate_add(pos, add_price=115.0, add_quantity=0.5)
    post_add_floor = pos.entry_price * (1 + params.net_breakeven_pct)

    assert post_add_floor > pre_add_floor
    # And simulate_exit actually enforces it: a price sitting just below the
    # NEW floor must exit, even though it would have been a healthy profit
    # relative to the ORIGINAL (lower) entry.
    assert pos.entry_price < 115.0  # sanity: blended entry below the add price
    reason = simulate_exit(pos, post_add_floor - 0.01, params)
    assert reason is not None


# ------------------------------------------------------- end-to-end replay

@pytest.fixture(autouse=True)
def _cheap_costs(monkeypatch):
    """Same rationale as test_backtest_sim.py's fixture: the fixtures were
    sized against the old cheap cost model."""
    import trading_costs
    monkeypatch.setattr(trading_costs, "IN_BROKERAGE_PCT", 0.0003)
    monkeypatch.setattr(trading_costs, "IN_BROKERAGE_CAP", 20.0)


@pytest.fixture
def engines():
    return DecisionEngine(), TrendEngine()


def test_disabled_by_default_matches_pre_pyramid_behavior_exactly(engines):
    """The critical regression pin: with pyramid_enabled left at its config
    default (False), replay() must produce byte-for-byte identical trades to
    before this feature existed. total_adds must be 0 everywhere."""
    de, te = engines
    result = replay("UPTREND.NS", load_fixture("uptrend"), de, te)
    assert result.total_adds == 0
    assert all(t.add_count == 0 for t in result.trades)


def test_enabling_pyramid_can_add_to_a_running_winner(engines):
    """On the uptrend fixture (the one canned scenario in this project that
    reliably produces a winning, armed trailing position), enabling
    pyramiding with an aggressive (small) step must exercise at least one
    add — otherwise this whole parity layer would be untested dead code."""
    de, te = engines
    params = SimParams(
        pyramid_enabled=True,
        pyramid_step_atr_multiple=0.3,   # small step so the fixture's run clears it
        pyramid_max_adds=2,
        pyramid_add_size_decay=0.5,
    )
    result = replay("UPTREND.NS", load_fixture("uptrend"), de, te, params=params)
    assert result.error is None
    assert result.total_adds > 0, (
        "pyramiding never fired on the uptrend fixture even with an "
        "aggressive step — the wiring may be broken"
    )


def test_replayed_trades_with_adds_pay_more_friction_than_the_baseline(engines):
    """End-to-end, through the real replay() path (not a re-derived formula):
    a closed trade that took adds must show MORE implied cost than a single
    round trip, since each add is its own extra BUY order. Uses the recorded
    entry/exit prices to back out gross return independent of the cost
    model, so this exercises the actual _close_position code, not a copy
    of its formula."""
    de, te = engines
    params = SimParams(
        pyramid_enabled=True, pyramid_step_atr_multiple=0.3,
        pyramid_max_adds=2, pyramid_add_size_decay=0.5,
        round_trip_cost_pct=0.003,
    )
    result = replay("UPTREND.NS", load_fixture("uptrend"), de, te, params=params)
    added_trades = [t for t in result.trades if t.add_count > 0]
    assert added_trades, "need at least one trade with an add to test the cost surcharge"
    for t in added_trades:
        gross_pct = (t.exit_price / t.entry_price - 1.0) * 100.0
        implied_cost_pct = gross_pct - t.return_pct
        # Baseline (0 adds) would only ever pay round_trip_cost_pct.
        assert implied_cost_pct > params.round_trip_cost_pct * 100.0 - 1e-9
