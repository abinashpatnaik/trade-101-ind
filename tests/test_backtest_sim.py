"""Backtest simulator tests: exit-math parity with OrderExecutor and
end-to-end replay behavior on canned fixtures."""

import os

import pandas as pd
import pytest

from agents.backtest_sim import (
    SimParams,
    _Position,
    replay,
    simulate_exit,
    verdict,
    SimResult,
)
from decision_engine import DecisionEngine
from trend_engine import TrendEngine

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


@pytest.fixture(autouse=True)
def _cheap_costs(monkeypatch):
    """Pin a low brokerage so the canned fixtures still produce trades.

    These tests assert replay MECHANICS (a trade opens, vetting rejects a
    crash, returns come out net of costs) on synthetic bars whose moves were
    sized against the old cheap cost model. After the 28-Jul-2026 contract
    note corrected brokerage to 0.5%/leg, the friction gate blocked every
    entry and the fixtures produced zero trades — which tested nothing.
    """
    import trading_costs

    monkeypatch.setattr(trading_costs, "IN_BROKERAGE_PCT", 0.0003)
    monkeypatch.setattr(trading_costs, "IN_BROKERAGE_CAP", 20.0)


def load_fixture(name: str) -> pd.DataFrame:
    return pd.read_csv(
        os.path.join(FIXTURES, f"{name}.csv"), index_col="Datetime", parse_dates=True
    )


# ----------------------------------------------------------------------
# Exit math — hand-computed cases pinned against check_exit_conditions
# ----------------------------------------------------------------------

def _pos(entry=100.0, stop=97.5, target=1099.0, trail=0.015, high=None):
    return _Position(
        entry_ts="t0",
        entry_price=entry,
        stop_loss_price=stop,
        take_profit_price=target,
        initial_trailing_pct=trail,
        high_water=high if high is not None else entry,
    )


def _params():
    # IN-market values: stop 2.5%, lock +0.5%, gap base 1.0%.
    # Cost pinned to 0 here — these tests pin the trailing GEOMETRY;
    # cost-floor behavior has its own tests below.
    return SimParams(
        stop_loss_pct=0.025,
        take_profit_pct=9.99,
        profit_lock_threshold=0.005,
        trailing_gap_base=0.010,
        round_trip_cost_pct=0.0,
    )


def test_hard_stop_fires():
    pos = _pos()
    assert simulate_exit(pos, 97.4, _params()) == "STOP_LOSS"


def test_no_exit_in_patience_zone():
    # Small dip above the stop, profit lock not armed -> no exit
    pos = _pos()
    assert simulate_exit(pos, 99.0, _params()) is None
    # High-water is a one-way ratchet: a dip must NOT lower it. This used to
    # reset to 99.0 ("recovery-point trailing"), which erased the peak and left
    # the trail unreachable — see tests/test_profit_lock_latch.py.
    assert pos.high_water == 100.0
    assert pos.lock_armed is False


def test_take_profit_fires():
    pos = _pos(target=101.0)
    assert simulate_exit(pos, 101.5, _params()) == "TAKE_PROFIT"


def test_profit_lock_trailing_exit():
    """After a +2% run, a pullback while still armed (gain >= +0.5%) exits at
    the graduated trailing trigger, locking profit."""
    params = _params()
    pos = _pos(trail=0.015)
    # Bar 1: +2.0% — lock armed, high=102.0
    assert simulate_exit(pos, 102.0, params) is None
    assert pos.high_water == 102.0
    # Bar 2: falls to +0.9% (still armed). gain_from_high=2% => gap=base*0.50
    # trigger = max(102.0*(1-0.0075), 100.0) = 101.235 >= price -> exit
    assert simulate_exit(pos, 100.9, params) == "TRAILING_STOP"


def test_profit_lock_graduated_gap_tightens():
    """+3.5% run then a shallow pullback hits the tightened (0.33x) gap."""
    params = _params()
    pos = _pos(trail=0.015)
    assert simulate_exit(pos, 103.5, params) is None  # high=103.5, gain_high=3.5%
    # gap = 0.015*0.33 = 0.00495 -> trigger = 103.5*0.99505 = 102.988
    assert simulate_exit(pos, 102.9, params) == "TRAILING_STOP"


def test_lock_stays_armed_below_threshold():
    """Parity with the executor: the profit-lock is a one-way LATCH. Falling
    back under the arm threshold must NOT disarm it and must NOT reset the
    high-water mark.

    This test previously asserted the opposite (disarm + reset). That behaviour
    was the bug: it made the trailing trigger unreachable and discarded the
    peak, so a locked-in gain could ride all the way down to the hard stop.
    """
    params = _params()
    pos = _pos(trail=0.015)
    assert simulate_exit(pos, 100.6, params) is None  # lock armed at +0.6%
    assert pos.high_water == 100.6
    assert pos.lock_armed is True
    # +0.3%: below the arm threshold and above the break-even floor, so nothing
    # fires — which is what lets us observe that the latch is still set and the
    # high-water mark was not rewritten.
    assert simulate_exit(pos, 100.3, params) is None
    assert pos.high_water == 100.6                    # ratchet: never lowered
    assert pos.lock_armed is True
    # Drop to the break-even floor and the still-armed trail does its job.
    floor = 100.0 * (1 + params.net_breakeven_pct)
    assert simulate_exit(pos, floor - 0.01, params) is not None


def test_high_water_is_preserved_through_a_dip():
    params = _params()
    pos = _pos()
    simulate_exit(pos, 100.7, params)   # lock armed, high=100.7
    simulate_exit(pos, 100.65, params)  # small dip, still above trigger? gap=base -> trigger=max(100.7*0.99, 100)=100.0 -> no exit
    assert pos.high_water == 100.7      # high preserved while lock active


# ----------------------------------------------------------------------
# End-to-end replay on fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def engines():
    return DecisionEngine(), TrendEngine()


def test_uptrend_trades_and_wins(engines):
    de, te = engines
    result = replay("UPTREND.NS", load_fixture("uptrend"), de, te)
    assert result.error is None
    assert result.n_trades >= 1
    assert result.total_return_pct > 0
    assert verdict(result) == "PASS"


def test_chop_is_blocked_by_sniper_gates(engines):
    de, te = engines
    result = replay("CHOP.NS", load_fixture("chop"), de, te)
    assert result.error is None
    # Choppy, low-ADX, flat-volume tape must not generate entries
    assert result.n_trades == 0
    assert verdict(result) == "PASS"  # no trades = neutral pass


def test_crash_fails_vetting(engines):
    de, te = engines
    # The crash fixture spikes to RSI ~99 before collapsing; the live overbought
    # guard (rsi_overbought_block) would correctly block that blow-off entry, so
    # it must be disabled here — this test pins the vetting FAIL-on-loss verdict,
    # which requires the losing trade to actually be taken. Save/restore so the
    # shared config singleton doesn't leak into other tests.
    prev = de._sig.rsi_overbought_block
    de._sig.rsi_overbought_block = 0.0
    try:
        result = replay("CRASH.NS", load_fixture("crash"), de, te)
    finally:
        de._sig.rsi_overbought_block = prev
    assert result.error is None
    assert result.n_trades >= 1
    assert result.total_return_pct < 0
    assert any(t.exit_reason in ("STOP_LOSS", "EOD") for t in result.trades)
    assert verdict(result) == "FAIL"


def test_insufficient_data_is_neutral(engines):
    de, te = engines
    result = replay("TINY.NS", load_fixture("uptrend").head(20), de, te)
    assert result.error == "insufficient data"
    assert verdict(result) == "PASS"


def test_verdict_threshold():
    r = SimResult(symbol="X", n_trades=3, total_return_pct=0.5)
    assert verdict(r) == "PASS"
    assert verdict(r, ev_threshold_pct=1.0) == "FAIL"


# ----------------------------------------------------------------------
# Cost-awareness
# ----------------------------------------------------------------------

def test_cost_floor_lifts_breakeven():
    """With costs, the profit-lock floor sits at entry×(1+cost): a pullback
    to gross break-even exits ABOVE entry, not at it."""
    params = _params()
    params.round_trip_cost_pct = 0.005  # 0.5%
    pos = _pos(trail=0.015)
    assert simulate_exit(pos, 102.0, params) is None  # armed, high=102
    # gap=base*0.50 -> trigger=max(102*0.9925, 100*1.005)=101.235; price
    # 100.9 <= trigger -> exits while still above the NET break-even.
    assert simulate_exit(pos, 100.9, params) == "TRAILING_STOP"


def test_returns_are_net_of_costs(engines):
    de, te = engines
    df = load_fixture("uptrend")
    gross = replay("X.NS", df, de, te, SimParams(round_trip_cost_pct=0.0))
    net = replay("X.NS", df, de, te, SimParams(round_trip_cost_pct=0.01))
    assert gross.n_trades >= 1 and net.n_trades >= 1
    # Every net trade is shaved by 1% (cost floor may also shift exits, so
    # only the direction is pinned, not the exact delta).
    assert net.total_return_pct < gross.total_return_pct


def test_default_cost_is_nonzero():
    assert SimParams().round_trip_cost_pct > 0.001  # slippage floor at minimum
