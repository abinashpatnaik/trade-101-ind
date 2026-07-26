"""PDT guard tests: day-trade counting and entry-slot budgeting.

Guard tests that involve "today" FREEZE the module clock to a fixed Wednesday
instead of using the real date. Dating test trades with date.today() rotted on
weekends: on a Saturday the guard's 5-business-day window ends Friday, so
"today's" trades fell outside it and every count came up short — and the
closed-reservation check compares against literal today, so shifting the trade
dates alone cannot fix it. Freezing the clock keeps the guard's own window
logic (safety-critical for the live US account) completely untouched.
"""

from datetime import date

import pytest

import agents.pdt_guard as pdt_mod
from agents.pdt_guard import PDTGuard, count_day_trades, last_n_business_days


def _t(d, sym, action):
    return {"date": d, "symbol": sym, "action": action}


TODAY = date(2026, 7, 15)  # a Wednesday
WINDOW = last_n_business_days(5, TODAY)  # Jul 9,10,13,14,15


class _FrozenDate(date):
    """date whose today() is pinned to TODAY (a mid-week trading day)."""

    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture()
def frozen_today(monkeypatch):
    """Pin agents.pdt_guard's clock to TODAY; returns its YYYY-MM-DD string."""
    monkeypatch.setattr(pdt_mod, "date", _FrozenDate)
    return TODAY.strftime("%Y-%m-%d")


def test_business_day_window_skips_weekends():
    assert WINDOW == {"2026-07-09", "2026-07-10", "2026-07-13", "2026-07-14", "2026-07-15"}


def test_buy_and_sell_same_day_is_one_day_trade():
    trades = [_t("2026-07-14", "AAPL", "BUY"), _t("2026-07-14", "AAPL", "SELL")]
    assert count_day_trades(trades, WINDOW) == 1


def test_overnight_round_trip_is_not_a_day_trade():
    trades = [_t("2026-07-13", "AAPL", "BUY"), _t("2026-07-14", "AAPL", "SELL")]
    assert count_day_trades(trades, WINDOW) == 0


def test_outside_window_ignored():
    trades = [_t("2026-07-06", "AAPL", "BUY"), _t("2026-07-06", "AAPL", "SELL")]
    assert count_day_trades(trades, WINDOW) == 0


def test_multiple_round_trips_same_symbol_same_day():
    trades = [
        _t("2026-07-15", "TSLA", "BUY"), _t("2026-07-15", "TSLA", "SELL"),
        _t("2026-07-15", "TSLA", "BUY"), _t("2026-07-15", "TSLA", "SELL"),
    ]
    assert count_day_trades(trades, WINDOW) == 2


def test_unmatched_buy_is_not_counted():
    trades = [_t("2026-07-15", "NVDA", "BUY")]
    assert count_day_trades(trades, WINDOW) == 0


class FakeDB:
    def __init__(self, trades):
        self._trades = trades

    def get_trades(self, limit=200):
        return self._trades


def test_guard_blocks_when_budget_used(frozen_today):
    trades = []
    for sym in ("A", "B", "C"):  # 3 day trades today = budget gone
        trades += [_t(frozen_today, sym, "BUY"), _t(frozen_today, sym, "SELL")]
    guard = PDTGuard(FakeDB(trades), max_day_trades=3)
    assert guard.slots_available() == 0
    assert guard.can_open_new_position() is False


def test_guard_allows_with_budget(frozen_today):
    trades = [_t(frozen_today, "A", "BUY"), _t(frozen_today, "A", "SELL")]  # 1 used
    guard = PDTGuard(FakeDB(trades), max_day_trades=3)
    assert guard.slots_available() == 2
    assert guard.can_open_new_position() is True


def test_open_position_reserves_slot():
    guard = PDTGuard(FakeDB([]), max_day_trades=3)
    assert guard.slots_available() == 3
    guard.note_buy("AAPL")   # open, unclosed -> reserves 1
    guard.note_buy("MSFT")
    assert guard.slots_available() == 1


def test_closed_reservation_moves_to_used_not_double_counted(frozen_today):
    trades = [_t(frozen_today, "AAPL", "BUY"), _t(frozen_today, "AAPL", "SELL")]  # closed = 1 used
    guard = PDTGuard(FakeDB(trades), max_day_trades=3)
    guard.note_buy("AAPL")   # was opened by us, now closed — no longer reserved
    assert guard.slots_available() == 2


def test_db_failure_fails_safe():
    class BrokenDB:
        def get_trades(self, limit=200):
            raise RuntimeError("db locked")

    guard = PDTGuard(BrokenDB(), max_day_trades=3)
    assert guard.slots_available() == 0
    assert guard.can_open_new_position() is False
