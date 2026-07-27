"""
Daily P&L must exclude external cash transfers.

Alpaca reports daily P&L as ``equity - last_equity``. A deposit inflates that
directly: on 2026-07-27 a $53.31 funding turned a −$0.73 trading day into a
reported +$52.58 — a 72x overstatement, and the dashboard showed it as profit.
Every future deposit would have done the same.

These tests pin the arithmetic and the failure modes without touching the
network (alpaca-py isn't installed locally, so the connector import is skipped
there and these run in-container).
"""

import pytest

pytest.importorskip("alpaca")

from alpaca_connector import AlpacaConnector  # noqa: E402


class _Acct:
    portfolio_value = "101.16"
    cash = "101.16"
    equity = "101.16"
    last_equity = "48.58"
    buying_power = "101.16"


def _conn(transfers, monkeypatch):
    c = AlpacaConnector.__new__(AlpacaConnector)
    c.trading_client = type("T", (), {"get_account": staticmethod(lambda: _Acct())})()
    monkeypatch.setattr(c, "net_cash_transfers_today", lambda: transfers)
    return c


def test_deposit_is_not_counted_as_profit(monkeypatch):
    """The real 2026-07-27 case: a losing day reported as a big win."""
    c = _conn(53.31, monkeypatch)
    assert c.get_account_summary()["DailyPnL"] == pytest.approx(-0.73, abs=0.01)


def test_no_transfer_leaves_pnl_untouched(monkeypatch):
    c = _conn(0.0, monkeypatch)
    assert c.get_account_summary()["DailyPnL"] == pytest.approx(52.58, abs=0.01)


def test_withdrawal_does_not_manufacture_a_loss(monkeypatch):
    """A −$20 withdrawal must not read as a $20 trading loss."""
    c = _conn(-20.0, monkeypatch)
    assert c.get_account_summary()["DailyPnL"] == pytest.approx(72.58, abs=0.01)


def test_transfer_lookup_failure_fails_open(monkeypatch):
    """A dead activities endpoint must not corrupt P&L further — return 0.0
    and leave the (imperfect) broker number rather than inventing one."""
    c = AlpacaConnector.__new__(AlpacaConnector)
    c.trading_client = type("T", (), {"get_account": staticmethod(lambda: _Acct())})()

    def _boom(*a, **k):
        raise RuntimeError("activities endpoint down")

    monkeypatch.setattr("requests.get", _boom)
    assert c.net_cash_transfers_today() == 0.0
    assert c.get_account_summary()["DailyPnL"] == pytest.approx(52.58, abs=0.01)


def test_only_todays_transfers_count(monkeypatch):
    """Yesterday's deposit must not keep suppressing today's P&L."""
    from datetime import date

    class _R:
        status_code = 200

        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return [
                {"date": date.today().isoformat(), "net_amount": "53.31"},
                {"date": "2026-06-23", "net_amount": "52.79"},   # older funding
            ]

    monkeypatch.setattr("requests.get", lambda *a, **k: _R())
    c = AlpacaConnector.__new__(AlpacaConnector)
    assert c.net_cash_transfers_today() == pytest.approx(53.31)


def test_result_is_cached_per_day(monkeypatch):
    """get_account_summary runs every loop; the HTTP call must not."""
    from datetime import date

    calls = {"n": 0}

    class _R:
        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            calls["n"] += 1
            return [{"date": date.today().isoformat(), "net_amount": "10.00"}]

    monkeypatch.setattr("requests.get", lambda *a, **k: _R())
    c = AlpacaConnector.__new__(AlpacaConnector)
    assert c.net_cash_transfers_today() == pytest.approx(10.0)
    assert c.net_cash_transfers_today() == pytest.approx(10.0)
    assert calls["n"] == 1, "second call must hit the cache, not the network"
