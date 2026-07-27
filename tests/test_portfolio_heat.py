"""
Portfolio heat budget.

Fixed per-trade risk left capital deployment hostage to how many signals
happened to fire: one qualifying setup deployed only 20% of NAV, five deployed
90%. On 2026-07-27 the US account traded a single position all day while four
slots sat empty.

The heat budget caps the SUM of open risk instead, so each trade may take a
full allocation while few are open, and the budget itself limits concurrency.
At 0.75%/trade and 2.25% total: three positions of 0.75%, the fourth refused.

Crucially this bounds concentration — it does NOT let one lone position swell
to fill the account, which would put a single stop-out (2.25% of NAV) through
the 2% daily halt.
"""

import pytest

from decision_engine import DecisionEngine


@pytest.fixture()
def engine(monkeypatch):
    e = DecisionEngine()
    monkeypatch.setattr(e._risk, "max_risk_per_trade_pct", 0.0075, raising=False)
    monkeypatch.setattr(e._risk, "max_portfolio_heat_pct", 0.0225, raising=False)
    return e


def _positions(n):
    return {f"SYM{i}": {"quantity": 1, "avg_cost": 100.0} for i in range(n)}


# ------------------------------------------------------- budget arithmetic
@pytest.mark.parametrize("n_open,expected", [
    (0, 0.0075),   # first position: full allocation
    (1, 0.0075),   # second: still full (0.75 + 0.75 = 1.5 <= 2.25)
    (2, 0.0075),   # third: exactly exhausts the budget
    (3, 0.0),      # fourth: refused, budget spent
    (4, 0.0),      # and stays refused
])
def test_available_budget_by_open_count(engine, n_open, expected):
    assert engine.available_risk_pct(_positions(n_open)) == pytest.approx(expected)


def test_heat_budget_never_exceeds_the_cap(engine):
    """Sum of allocations across the positions it permits must fit the cap."""
    total = 0.0
    for n in range(10):
        total += engine.available_risk_pct(_positions(n))
    assert total <= engine._risk.max_portfolio_heat_pct + 1e-9


# ----------------------------------------------------------- sizing effect
def test_lone_position_deploys_more_than_before(engine):
    """The whole point: one signal should deploy ~30% of NAV, not 20%."""
    nav, price, atr = 17_490.0, 280.0, 3.5
    qty = engine._apply_risk_cap(10_000.0, nav, price, atr, _positions(0))
    deployed = qty * price / nav
    assert 0.25 < deployed < 0.35, f"expected ~30% deployed, got {deployed:.0%}"


def test_deployment_scales_with_position_count(engine):
    """1 -> ~30%, 2 -> ~60%, 3 -> ~90% of NAV."""
    nav, price, atr = 17_490.0, 280.0, 3.5
    cumulative = 0.0
    for n in range(3):
        qty = engine._apply_risk_cap(10_000.0, nav, price, atr, _positions(n))
        cumulative += qty * price / nav
    assert 0.80 < cumulative < 0.95, f"3 positions should reach ~90%, got {cumulative:.0%}"


def test_fourth_position_is_refused(engine):
    qty = engine._apply_risk_cap(10_000.0, 17_490.0, 280.0, 3.5, _positions(3))
    assert qty == 0.0


def test_single_stop_out_stays_under_the_daily_halt(engine):
    """A lone position must NOT be able to breach the 2% daily halt alone —
    this is what separates a heat budget from 'just fill the account'."""
    nav, price, atr = 17_490.0, 280.0, 3.5
    qty = engine._apply_risk_cap(10_000.0, nav, price, atr, _positions(0))
    loss = qty * price * engine.dynamic_stop_pct(price, atr)
    assert loss <= nav * 0.0075 + 1e-6
    assert loss < nav * engine._risk.max_daily_loss_pct


# ------------------------------------------------------------- safe defaults
def test_defaults_disabled():
    from config import config
    assert getattr(config.risk, "max_portfolio_heat_pct", 0.0) == 0.0


def test_no_heat_cap_falls_back_to_per_trade_only(monkeypatch):
    """heat=0 must behave exactly as before this feature existed."""
    e = DecisionEngine()
    monkeypatch.setattr(e._risk, "max_risk_per_trade_pct", 0.005, raising=False)
    monkeypatch.setattr(e._risk, "max_portfolio_heat_pct", 0.0, raising=False)
    for n in (0, 3, 10):
        assert e.available_risk_pct(_positions(n)) == pytest.approx(0.005)


def test_per_trade_disabled_disables_everything(monkeypatch):
    e = DecisionEngine()
    monkeypatch.setattr(e._risk, "max_risk_per_trade_pct", 0.0, raising=False)
    monkeypatch.setattr(e._risk, "max_portfolio_heat_pct", 0.0225, raising=False)
    assert e.available_risk_pct(_positions(0)) == 0.0
    # and the cap must pass quantity through untouched
    assert e._apply_risk_cap(123.0, 17_490.0, 280.0, 3.5, _positions(0)) == 123.0
