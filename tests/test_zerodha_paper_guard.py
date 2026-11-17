"""
TRADING_MODE=paper must not send real BUYs to Zerodha.

Zerodha has no paper account. config.kite.paper_mode was read from
TRADING_MODE but never checked, so the India agent placed real orders while
"in paper mode". Paper now blocks every BUY; SELLs still go through so real
positions can always be closed. Also pins the fixed stop-order path, which
referenced an undefined exchange_code and so could never place a stop.
"""

import sys

import pytest

from zerodha_connector import ZerodhaConnector


def _cfg():
    # Other tests importlib.reload(config); the connector reads the CURRENT
    # module's config at call time, so patch that one, not a stale import.
    return sys.modules["config"].config


class _Kite:
    def __init__(self):
        self.orders_placed = []

    def place_order(self, **kw):
        self.orders_placed.append(kw)
        return "K1"


@pytest.fixture()
def conn(monkeypatch):
    c = ZerodhaConnector.__new__(ZerodhaConnector)
    c.kite = _Kite()
    monkeypatch.setattr(c, "get_current_price", lambda s: 100.0, raising=False)
    monkeypatch.setattr(c, "get_positions", lambda: {"ABC.NS": {"quantity": 5}}, raising=False)
    monkeypatch.setattr(_cfg().risk, "allow_short_selling", False, raising=False)
    return c


def test_paper_blocks_buy(conn, monkeypatch):
    monkeypatch.setattr(_cfg().kite, "paper_mode", True)
    assert conn.place_market_order("ABC.NS", "BUY", 5) is None
    assert conn.place_limit_order("ABC.NS", "BUY", 5, 100.0) is None
    assert conn.kite.orders_placed == []


def test_paper_still_allows_sells_and_sell_stops(conn, monkeypatch):
    monkeypatch.setattr(_cfg().kite, "paper_mode", True)
    assert conn.place_market_order("ABC.NS", "SELL", 5) == "K1"
    assert conn.place_stop_order("ABC.NS", "SELL", 5, 95.0) == "K1"
    stop = conn.kite.orders_placed[-1]
    assert stop["order_type"] == "SL" and stop["exchange"] == "NSE"


def test_live_allows_buy(conn, monkeypatch):
    monkeypatch.setattr(_cfg().kite, "paper_mode", False)
    assert conn.place_market_order("ABC.NS", "BUY", 5) == "K1"
