"""
Broker-side backstop stops.

Every exit the executor manages is a software stop, so before this a crashed
container, a dead host or a deploy restart left open positions with no
protection. Each tracked position now also gets a real SELL stop resting at
the broker, below the software hard stop.

These tests pin:
  - a BUY arms a backstop below the software stop, sized to the shares HELD
  - no backstop for shares not yet held (a bigger stop could open a short)
  - a software sell cancels the backstop first
  - if the backstop already filled, close_position reports closed, no 2nd sell
  - an expired/missing backstop is re-armed on sync; a failed order read is
    never treated as "missing" (no duplicate stops)
  - a pyramid add replaces the backstop with one covering the new quantity
  - pruning an orphaned position cancels its backstop
  - BROKER_BACKSTOP_ENABLED=false turns it all off
"""

from __future__ import annotations

import pytest

from config import config
from decision_engine import Decision
from order_executor import OrderExecutor


class StopBroker:
    """Fake broker that tracks resting stop orders and positions."""

    def __init__(self, held=None):
        self.held = dict(held or {})       # symbol -> qty
        self.open = {}                     # order_id -> (symbol, qty, price)
        self.market = []
        self.cancelled = []
        self.fail_reads = False
        self.fail_cancel = False
        self._n = 0

    def _id(self):
        self._n += 1
        return f"o{self._n}"

    def is_connected(self):
        return True

    def get_positions(self):
        return {s: {"quantity": q, "avg_cost": 100.0} for s, q in self.held.items() if q > 0}

    def get_current_price(self, symbol):
        return 100.0

    def place_market_order(self, symbol, action, quantity, **kw):
        self.market.append((symbol, action, quantity))
        if action == "BUY":
            self.held[symbol] = self.held.get(symbol, 0) + quantity
        else:
            self.held[symbol] = self.held.get(symbol, 0) - quantity
        return self._id()

    def place_stop_order(self, symbol, action, quantity, stop_price):
        oid = self._id()
        self.open[oid] = (symbol, quantity, stop_price)
        return oid

    def cancel_order(self, oid):
        if self.fail_cancel:
            return False
        self.cancelled.append(oid)
        self.open.pop(oid, None)
        return True

    def open_order_ids(self):
        if self.fail_reads:
            return None
        return set(self.open)

    def get_last_fill_price(self, symbol, side="sell"):
        return 97.5


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MARKET", "TESTBACKSTOP")
    monkeypatch.setattr(config.agent, "trades_csv", str(tmp_path / "trades.csv"))
    monkeypatch.setattr(config.risk, "broker_backstop_enabled", True)
    monkeypatch.setattr(config.risk, "broker_backstop_buffer_pct", 0.01)
    monkeypatch.setattr(config.risk, "stop_loss_pct", 0.025)
    monkeypatch.setattr(config.risk, "pyramid_enabled", True)


def _buy(ex, symbol="ABC", qty=10, stop=98.0):
    d = Decision(action="BUY", quantity=qty, confidence=0.9,
                 reason="t", stop_loss_price=stop, take_profit_price=110.0,
                 trailing_stop_pct=0.01, combined_score=0.9)
    assert ex.execute(d, symbol, 100.0)


def test_buy_arms_backstop_below_software_stop():
    b = StopBroker()
    ex = OrderExecutor(b)
    _buy(ex)
    [(sym, qty, price)] = b.open.values()
    assert sym == "ABC" and qty == 10
    assert price == round(98.0 * 0.99, 2)          # 1% under the software stop
    assert ex.open_orders["ABC"].stop_loss_order_id in b.open


def test_no_backstop_for_shares_not_yet_held():
    b = StopBroker()
    b.place_market_order = lambda *a, **k: "unfilled"   # entry not filled
    ex = OrderExecutor(b)
    _buy(ex)
    assert b.open == {}
    # Once the fill shows up, the next sync arms it.
    b.held["ABC"] = 10
    ex._last_backstop_check = None
    ex.sync_positions(b.get_positions(), b)
    assert len(b.open) == 1


def test_software_sell_cancels_backstop_first():
    b = StopBroker()
    ex = OrderExecutor(b)
    _buy(ex)
    oid = ex.open_orders["ABC"].stop_loss_order_id
    assert ex.close_position("ABC", 10)
    assert oid in b.cancelled and b.open == {}
    assert b.market[-1] == ("ABC", "SELL", 10)


def test_close_when_backstop_already_filled_sends_no_second_sell():
    b = StopBroker()
    ex = OrderExecutor(b)
    _buy(ex)
    # The backstop fired while the agent was down: shares gone, cancel fails.
    b.held["ABC"] = 0
    b.fail_cancel = True
    n_market = len(b.market)
    assert ex.close_position("ABC", 10) is True
    assert len(b.market) == n_market                # no extra SELL
    assert ex.pop_fill_price("ABC") == 97.5
    assert "ABC" not in ex.open_orders


def test_expired_backstop_is_rearmed_on_sync():
    b = StopBroker()
    ex = OrderExecutor(b)
    _buy(ex)
    b.open.clear()                                  # DAY stop expired overnight
    ex._last_backstop_check = None
    ex.sync_positions(b.get_positions(), b)
    assert len(b.open) == 1


def test_failed_order_read_never_places_duplicate():
    b = StopBroker()
    ex = OrderExecutor(b)
    _buy(ex)
    b.fail_reads = True
    ex._last_backstop_check = None
    ex.sync_positions(b.get_positions(), b)
    assert len(b.open) == 1


def test_pyramid_add_replaces_backstop_for_new_quantity():
    b = StopBroker()
    ex = OrderExecutor(b)
    _buy(ex)
    first = ex.open_orders["ABC"].stop_loss_order_id
    assert ex.execute_pyramid_add("ABC", 4, 103.0)
    assert first in b.cancelled
    [(sym, qty, _)] = b.open.values()
    assert qty == 14


def test_orphan_prune_cancels_backstop():
    b = StopBroker()
    ex = OrderExecutor(b)
    _buy(ex)
    oid = ex.open_orders["ABC"].stop_loss_order_id
    b.held["ABC"] = 0
    for _ in range(ex._ORPHAN_GRACE):
        ex.sync_positions(b.get_positions(), b)
    assert "ABC" not in ex.open_orders
    assert oid in b.cancelled


def test_disabled_places_nothing(monkeypatch):
    monkeypatch.setattr(config.risk, "broker_backstop_enabled", False)
    b = StopBroker()
    ex = OrderExecutor(b)
    _buy(ex)
    assert b.open == {}
