"""The intraday agent must not spend or manage the India growth bot's money
and shares, which live in the same Zerodha account."""

import json

import pytest

from zerodha_connector import ZerodhaConnector


class Kite:
    def margins(self):
        return {"equity": {"net": 60000.0}}

    def holdings(self):
        return [{"tradingsymbol": "TCS", "exchange": "NSE", "quantity": 7, "t1_quantity": 0,
                 "average_price": 3000.0, "last_price": 3100.0, "day_change": 10.0},
                {"tradingsymbol": "INFY", "exchange": "NSE", "quantity": 4, "t1_quantity": 0,
                 "average_price": 1500.0, "last_price": 1600.0, "day_change": 5.0}]

    def positions(self):
        return {"net": [{"tradingsymbol": "SBIN", "exchange": "NSE", "quantity": 10, "average_price": 800.0,
                         "last_price": 810.0, "m2m": 100.0}]}

    def ltp(self, instrument):
        raise RuntimeError("no quotes in tests")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("ZERODHA_IGNORE_HOLDINGS", raising=False)
    c = ZerodhaConnector()
    c.kite = Kite()
    c.growth_reserved_file = str(tmp_path / "in_growth_reserved.json")
    return c


def _reserve(c, cash, holdings):
    with open(c.growth_reserved_file, "w") as fh:
        json.dump({"cash": cash, "holdings": holdings}, fh)


def test_without_a_reservation_nothing_changes(conn):
    pos = conn.get_positions()
    assert pos["TCS.NS"]["quantity"] == 7 and pos["INFY.NS"]["quantity"] == 4
    assert conn.get_account_summary()["AvailableFunds"] == pytest.approx(60000.0)


def test_reserved_shares_and_cash_are_hidden(conn):
    _reserve(conn, 25000.0, {"TCS.NS": 7, "INFY.NS": 1})
    pos = conn.get_positions()
    assert "TCS.NS" not in pos                                   # wholly the growth bot's
    assert pos["INFY.NS"]["quantity"] == 3                        # 1 of 4 is the growth bot's
    assert pos["INFY.NS"]["market_value"] == pytest.approx(3 * 1600.0)
    assert pos["SBIN.NS"]["quantity"] == 10                       # intraday position untouched
    summary = conn.get_account_summary()
    assert summary["AvailableFunds"] == pytest.approx(35000.0)


def test_a_corrupt_reservation_file_is_ignored(conn):
    with open(conn.growth_reserved_file, "w") as fh:
        fh.write("{not json")
    assert conn.get_positions()["TCS.NS"]["quantity"] == 7
