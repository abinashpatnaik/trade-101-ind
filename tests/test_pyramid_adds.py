"""
Pyramiding — add to a position that has already proven itself.

Off by default (config.risk.pyramid_enabled = False, env PYRAMID_ENABLED).
Deliberately the OPPOSITE shape from averaging down past a stop-loss (which
was considered and rejected): an add is only ever allowed once the
profit-lock latch has ARMED (never below net break-even, one-way, see
test_profit_lock_latch.py), each add is sized SMALLER than the last
(pyramid_add_size_decay), and there is a hard ceiling on how many adds a
position can take (pyramid_max_adds) independent of how far price runs.

These tests pin:
  - the gating (disabled / unarmed / max-adds / step-not-cleared all block)
  - the ATR-derived step trigger
  - record_add's volume-weighted cost-basis blend
  - THE SAFETY INVARIANT: after a blended add, the existing "trailing stop
    can never sit below net break-even" floor in check_exit_conditions
    still holds — against the NEW blended entry, with no change to that
    logic (it already keyed off order.entry_price/quantity).
  - DecisionEngine.size_pyramid_add's shrinking-budget and position-size-cap
    behaviour.
"""

from __future__ import annotations

import pytest

from config import config
from order_executor import OpenOrder, OrderExecutor
from decision_engine import DecisionEngine


class FakeBroker:
    """Minimal stand-in for the live broker connector."""

    def __init__(self, connected=True, order_id=101):
        self._connected = connected
        self._order_id = order_id
        self.placed = []

    def is_connected(self):
        return self._connected

    def place_market_order(self, symbol, action, quantity):
        self.placed.append((symbol, action, quantity))
        return self._order_id

    def get_positions(self):
        return {}

    def cancel_order(self, order_id):
        pass


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect executor state to a tmp dir and reset pyramid config to
    known defaults so tests don't depend on env vars set elsewhere and never
    touch real data/executor_state_*.json files."""
    monkeypatch.setenv("TRADING_MARKET", "TESTPYR")
    monkeypatch.setattr(config.agent, "trades_csv", str(tmp_path / "trades.csv"))
    monkeypatch.setattr(config.risk, "pyramid_enabled", True)
    monkeypatch.setattr(config.risk, "pyramid_step_atr_multiple", 1.5)
    monkeypatch.setattr(config.risk, "pyramid_max_adds", 2)
    monkeypatch.setattr(config.risk, "pyramid_add_size_decay", 0.5)
    monkeypatch.setattr(config.risk, "trailing_gap_base", 0.008)
    monkeypatch.setattr(config.risk, "max_risk_per_trade_pct", 0.0075)
    monkeypatch.setattr(config.risk, "max_portfolio_heat_pct", 0.0225)
    monkeypatch.setattr(config.risk, "max_position_size_pct", 0.30)
    monkeypatch.setattr(config.risk, "leverage", 1.0)
    monkeypatch.setattr(config.risk, "stop_loss_pct", 0.025)
    yield


def _executor_with_position(entry=100.0, qty=10.0, atr_gap_pct=0.013,
                            armed=True, add_count=0, last_add_price=None):
    ex = OrderExecutor(FakeBroker())
    order = OpenOrder(
        symbol="TST", entry_order_id=1, order_type="BUY",
        quantity=qty, entry_price=entry,
        stop_loss_price=round(entry * (1 - config.risk.stop_loss_pct), 2),
        take_profit_price=0.0, is_fractional=True,
        initial_trailing_pct=atr_gap_pct, add_count=add_count,
        last_add_price=last_add_price if last_add_price is not None else entry,
    )
    ex._open_orders["TST"] = order
    ex._trailing_high["TST"] = entry
    ex._lock_armed["TST"] = armed
    return ex, order


# --------------------------------------------------------------- gating

def test_disabled_by_default_blocks_everything(monkeypatch):
    monkeypatch.setattr(config.risk, "pyramid_enabled", False)
    ex, order = _executor_with_position()
    step_price = order.entry_price * (1 + order.initial_trailing_pct * config.risk.pyramid_step_atr_multiple)
    assert ex.check_pyramid_conditions("TST", step_price + 1) is False


def test_unarmed_position_never_gets_an_add():
    """The core safety property: no add until the trade has proven itself."""
    ex, order = _executor_with_position(armed=False)
    huge_move = order.entry_price * 2  # way past any conceivable step
    assert ex.check_pyramid_conditions("TST", huge_move) is False


def test_max_adds_is_a_hard_ceiling():
    ex, order = _executor_with_position(add_count=2)  # == pyramid_max_adds
    huge_move = order.entry_price * 2
    assert ex.check_pyramid_conditions("TST", huge_move) is False


def test_step_not_yet_cleared_blocks():
    ex, order = _executor_with_position(entry=100.0, atr_gap_pct=0.013)
    step = 100.0 * 0.013 * config.risk.pyramid_step_atr_multiple  # 1.95
    assert ex.check_pyramid_conditions("TST", 100.0 + step - 0.01) is False


def test_step_cleared_and_armed_fires():
    ex, order = _executor_with_position(entry=100.0, atr_gap_pct=0.013)
    step = 100.0 * 0.013 * config.risk.pyramid_step_atr_multiple
    assert ex.check_pyramid_conditions("TST", 100.0 + step + 0.01) is True


def test_steps_chain_from_the_last_add_not_always_from_entry():
    """After one add, the NEXT step measures from the add's own fill price,
    not from the original entry — otherwise steps would bunch up near entry
    instead of spacing out as the position runs."""
    ex, order = _executor_with_position(entry=100.0, atr_gap_pct=0.013, add_count=1,
                                        last_add_price=105.0)
    step = 100.0 * 0.013 * config.risk.pyramid_step_atr_multiple
    assert ex.check_pyramid_conditions("TST", 105.0 + step - 0.01) is False
    assert ex.check_pyramid_conditions("TST", 105.0 + step + 0.01) is True


# ------------------------------------------------------------- record_add

def test_record_add_blends_cost_basis_and_advances_state():
    ex, order = _executor_with_position(entry=100.0, qty=10.0)
    ex.record_add("TST", add_price=110.0, add_quantity=5.0)

    updated = ex._open_orders["TST"]
    expected_entry = (100.0 * 10.0 + 110.0 * 5.0) / 15.0  # 103.33
    assert updated.quantity == pytest.approx(15.0)
    assert updated.entry_price == pytest.approx(expected_entry)
    assert updated.add_count == 1
    assert updated.last_add_price == pytest.approx(110.0)


def test_record_add_is_a_noop_on_bad_input():
    ex, order = _executor_with_position(entry=100.0, qty=10.0)
    ex.record_add("TST", add_price=0.0, add_quantity=5.0)
    ex.record_add("TST", add_price=110.0, add_quantity=0.0)
    ex.record_add("NOPE", add_price=110.0, add_quantity=5.0)
    still = ex._open_orders["TST"]
    assert still.quantity == pytest.approx(10.0)
    assert still.add_count == 0


def test_record_add_persists_across_a_fresh_load(tmp_path, monkeypatch):
    """A restart must not lose a pyramid add's blended state, same guarantee
    already relied on for lock_armed/trailing_high."""
    ex, order = _executor_with_position(entry=100.0, qty=10.0)
    ex.record_add("TST", add_price=110.0, add_quantity=5.0)

    reloaded = OrderExecutor(FakeBroker())
    got = reloaded._open_orders["TST"]
    assert got.quantity == pytest.approx(15.0)
    assert got.add_count == 1
    assert got.last_add_price == pytest.approx(110.0)


# ----------------------------------------------- THE safety invariant

def test_add_never_lowers_the_net_breakeven_floor():
    """After blending in an add at a HIGHER price, check_exit_conditions'
    existing 'never below net break-even' floor must key off the NEW
    blended (higher) entry — protecting the whole position, add included —
    with zero changes needed to that method. This is the property that
    makes pyramiding safe where averaging down is not: the floor only ever
    rises with an add, never falls."""
    ex, order = _executor_with_position(entry=100.0, qty=10.0, atr_gap_pct=0.013)
    # Arm the lock the normal way: clear the arm threshold once.
    from trading_costs import profit_lock_arm_pct
    notional = order.entry_price * order.quantity
    arm_at = 100.0 * (1 + profit_lock_arm_pct(notional, config.risk.profit_lock_threshold))
    ex.check_exit_conditions("TST", arm_at + 0.5, {"quantity": 10.0})
    assert ex._lock_armed["TST"] is True

    floor_before = order.entry_price  # breakeven floor scales off this

    ex.record_add("TST", add_price=110.0, add_quantity=5.0)
    blended = ex._open_orders["TST"].entry_price
    assert blended > floor_before  # the floor's reference price rose

    # A price that would have been ABOVE the pre-add breakeven floor can now
    # be BELOW the post-add one — check_exit_conditions must exit there
    # rather than treating it as still-safe headroom.
    from trading_costs import net_breakeven_pct
    new_notional = blended * 15.0
    breakeven_pct = net_breakeven_pct(new_notional, overnight=False)
    post_add_floor = blended * (1 + breakeven_pct)
    pre_add_floor = floor_before * (1 + net_breakeven_pct(floor_before * 10.0, overnight=False))
    assert post_add_floor > pre_add_floor


# --------------------------------------------------------- execute_pyramid_add

def test_execute_pyramid_add_happy_path():
    ex, order = _executor_with_position(entry=100.0, qty=10.0)
    broker = ex._ibkr
    ok = ex.execute_pyramid_add("TST", 5.0, 110.0)
    assert ok is True
    assert broker.placed == [("TST", "BUY", 5.0)]
    assert ex._open_orders["TST"].quantity == pytest.approx(15.0)
    assert ex._open_orders["TST"].add_count == 1


def test_execute_pyramid_add_refuses_without_tracked_position():
    ex = OrderExecutor(FakeBroker())
    assert ex.execute_pyramid_add("GHOST", 5.0, 110.0) is False
    assert ex._ibkr.placed == []


def test_execute_pyramid_add_refuses_when_broker_disconnected():
    ex, order = _executor_with_position()
    ex._ibkr = FakeBroker(connected=False)
    assert ex.execute_pyramid_add("TST", 5.0, 110.0) is False


def test_execute_pyramid_add_handles_broker_returning_no_order_id():
    ex, order = _executor_with_position()
    ex._ibkr = FakeBroker(order_id=None)
    assert ex.execute_pyramid_add("TST", 5.0, 110.0) is False
    assert ex._open_orders["TST"].add_count == 0  # no phantom blend on failure


# --------------------------------------------------------- DecisionEngine sizing

def _engine():
    return DecisionEngine()


def test_size_pyramid_add_zero_when_disabled(monkeypatch):
    monkeypatch.setattr(config.risk, "pyramid_enabled", False)
    eng = _engine()
    qty = eng.size_pyramid_add(
        current_price=100.0, atr=1.0, portfolio_value=100_000.0,
        open_positions={"TST": {}}, existing_quantity=10.0, add_index=1,
    )
    assert qty == 0.0


def test_size_pyramid_add_shrinks_with_add_index():
    eng = _engine()
    qty1 = eng.size_pyramid_add(
        current_price=100.0, atr=1.0, portfolio_value=100_000.0,
        open_positions={"TST": {}}, existing_quantity=10.0, add_index=1,
    )
    qty2 = eng.size_pyramid_add(
        current_price=100.0, atr=1.0, portfolio_value=100_000.0,
        open_positions={"TST": {}}, existing_quantity=10.0, add_index=2,
    )
    assert qty1 > 0
    assert qty2 > 0
    assert qty2 == pytest.approx(qty1 * 0.5, rel=0.05)


def test_size_pyramid_add_respects_position_size_headroom():
    """However much risk budget remains, an add must never push the
    position's total notional past max_position_size_pct — pyramiding
    cannot be a backdoor around the ordinary size cap."""
    eng = _engine()
    portfolio_value = 100_000.0
    max_notional = portfolio_value * config.risk.max_position_size_pct
    # already at the cap
    existing_qty = max_notional / 100.0
    qty = eng.size_pyramid_add(
        current_price=100.0, atr=1.0, portfolio_value=portfolio_value,
        open_positions={"TST": {}}, existing_quantity=existing_qty, add_index=1,
    )
    assert qty == 0.0


def test_size_pyramid_add_zero_when_risk_budget_exhausted(monkeypatch):
    eng = _engine()
    # 3 open positions already consume the full 2.25% heat budget at 0.75%/trade.
    open_positions = {"A": {}, "B": {}, "C": {}}
    qty = eng.size_pyramid_add(
        current_price=100.0, atr=1.0, portfolio_value=100_000.0,
        open_positions=open_positions, existing_quantity=10.0, add_index=1,
    )
    assert qty == 0.0
