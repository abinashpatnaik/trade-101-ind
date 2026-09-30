"""
Broker-sync reconciliation: mid-position quantity growth.

update()'s reconciliation loop only ever recorded a trade on a binary
open/close transition (symbol newly appears / symbol vanishes). A pyramid
add (or any other top-up that grows an already-open position without ever
closing it) fell through both branches: no trade was logged, so it never
appeared in Execution History, and the eventual full-exit SELL then didn't
match the original entry's BUY quantity. self.open_positions was still
overwritten with the broker's real quantity at the end of update(), so Live
Positions was always correct — only the trades DB was missing the delta,
which is what produced the mismatch.
"""

import pytest

from portfolio_tracker import PortfolioTracker


class _FakeIBKR:
    def __init__(self, summary, positions):
        self._summary = summary
        self._positions = positions

    def get_account_summary(self):
        return self._summary

    def get_positions(self):
        return self._positions


@pytest.fixture()
def tracker(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("PAPER_TRADING_ENABLED", raising=False)
    monkeypatch.setenv("TRADING_MARKET", "US")
    t = PortfolioTracker()
    monkeypatch.setattr(t, "_dump_local_positions", lambda: None)
    monkeypatch.setattr(t, "_dump_local_summary", lambda: None)
    # record_trade() only appends SELL trades to self.closed_trades — BUYs
    # are logged straight to the DB via _persist_trade. Intercept there so
    # both directions are visible to the test uniformly.
    t.persisted_trades = []
    monkeypatch.setattr(t, "_persist_trade", lambda trade: t.persisted_trades.append(trade))
    return t


def _summary():
    return {
        "NetLiquidation": 100000.0,
        "AvailableFunds": 50000.0,
        "DailyPnL": 0.0,
    }


def test_grown_position_records_buy_for_the_delta(tracker):
    # Mirrors the real INTU case: an existing fractional position whose
    # broker-side quantity grew via a pyramid add.
    tracker._is_first_update = False
    tracker.open_positions = {
        "INTU": {"quantity": 0.0839, "avg_cost": 268.02, "market_value": 22.49},
    }
    tracker.pending_reasons["INTU"] = "PYRAMID_ADD"

    grown = {
        "INTU": {"quantity": 0.1106, "avg_cost": 268.91, "market_value": 29.74},
    }
    ibkr = _FakeIBKR(_summary(), grown)

    tracker.update(ibkr)

    buys = [t for t in tracker.persisted_trades if t.symbol == "INTU" and t.action == "BUY"]
    assert len(buys) == 1
    trade = buys[0]
    assert trade.quantity == pytest.approx(0.0267, abs=1e-4)
    # Implied price backed out of the broker's blended avg_cost:
    # (0.1106*268.91 - 0.0839*268.02) / 0.0267 ~= 271.6
    assert trade.price == pytest.approx(271.6, abs=0.5)
    assert trade.exit_reason == "PYRAMID_ADD"

    # Live Positions must reflect the broker's real (grown) quantity.
    assert tracker.open_positions["INTU"]["quantity"] == pytest.approx(0.1106)

    # The pending reason is consumed, not left dangling for the next sync.
    assert "INTU" not in tracker.pending_reasons


def test_grown_position_without_pending_reason_uses_fallback_reason(tracker):
    tracker._is_first_update = False
    tracker.open_positions = {"AAPL": {"quantity": 1.0, "avg_cost": 200.0}}

    grown = {"AAPL": {"quantity": 1.5, "avg_cost": 202.0}}
    tracker.update(_FakeIBKR(_summary(), grown))

    buys = [t for t in tracker.persisted_trades if t.symbol == "AAPL" and t.action == "BUY"]
    assert len(buys) == 1
    assert buys[0].exit_reason == "BROKER_SYNC_ADD"
    assert buys[0].quantity == pytest.approx(0.5)


def test_unchanged_position_records_no_trade(tracker):
    tracker._is_first_update = False
    tracker.open_positions = {"MSFT": {"quantity": 2.0, "avg_cost": 400.0}}

    same = {"MSFT": {"quantity": 2.0, "avg_cost": 400.0}}
    tracker.update(_FakeIBKR(_summary(), same))

    assert tracker.persisted_trades == []
    assert tracker.open_positions["MSFT"]["quantity"] == pytest.approx(2.0)


def test_shrunk_position_is_not_treated_as_a_growth_add(tracker):
    # A partial sell/exit shrinks quantity — must not be misread as an add.
    tracker._is_first_update = False
    tracker.open_positions = {"NVDA": {"quantity": 2.0, "avg_cost": 100.0}}

    shrunk = {"NVDA": {"quantity": 1.0, "avg_cost": 100.0}}
    tracker.update(_FakeIBKR(_summary(), shrunk))

    buys = [t for t in tracker.persisted_trades if t.symbol == "NVDA" and t.action == "BUY"]
    assert buys == []
    assert tracker.open_positions["NVDA"]["quantity"] == pytest.approx(1.0)
