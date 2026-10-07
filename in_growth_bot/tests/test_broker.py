import os

import pytest

from in_growth_bot import broker as B
from in_growth_bot.config import settings
from in_growth_bot.planner import Order


def test_limit_prices_sit_on_the_nse_tick_grid():
    assert B.limit_price(200.0, "BUY") == pytest.approx(201.0)            # 0.01 tick
    assert B.limit_price(3000.0, "BUY") == pytest.approx(3015.0)          # 0.10 tick
    p = B.limit_price(3001.23, "BUY")
    assert p >= 3001.23 * 1.005 and abs(p * 10 - round(p * 10)) < 1e-6
    s = B.limit_price(14000.0, "SELL")
    assert s <= 14000 * 0.995 and s == int(s)                             # 1.00 tick


class Kite:
    def __init__(self, fail_place=False, placed_anyway=False, held=10, fill="COMPLETE", filled=None):
        self.fail_place, self.placed_anyway = fail_place, placed_anyway
        self.held, self.fill, self.filled = held, fill, filled
        self.placed, self.cancelled, self.token = [], [], None

    def set_access_token(self, t):
        self.token = t

    def profile(self):
        if self.token != "good":
            raise Exception("TokenException: invalid token")

    def ltp(self, k):
        return {k: {"last_price": 1000.0}}

    def holdings(self):
        return [{"tradingsymbol": "TCS", "exchange": "NSE", "quantity": self.held, "t1_quantity": 0}]

    def positions(self):
        return {"net": []}

    def place_order(self, **kw):
        self.placed.append(kw)
        if self.fail_place:
            raise TimeoutError("read timed out")
        return "o1"

    def orders(self):
        if not self.placed_anyway:
            return []
        kw = self.placed[-1]
        return [{"order_id": "o9", "tradingsymbol": kw["tradingsymbol"], "transaction_type": kw["transaction_type"],
                 "quantity": kw["quantity"], "tag": kw["tag"], "order_timestamp": None}]

    def order_history(self, oid):
        q = self.filled if self.filled is not None else self.placed[-1]["quantity"]
        status = self.fill if not self.cancelled else "CANCELLED"
        return [{"status": status, "filled_quantity": q, "average_price": 1004.0}]

    def cancel_order(self, variety, order_id):
        self.cancelled.append(order_id)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    monkeypatch.setattr(B, "FILL_WAIT_SECONDS", 0)


def test_buy_is_a_cnc_limit_order_and_reports_the_fill():
    k = Kite()
    fill = B.KiteBroker(kite=k).execute(Order("BUY", "TCS.NS", 5, 1000.0, "test"))
    o = k.placed[0]
    assert (o["product"], o["order_type"], o["exchange"], o["tradingsymbol"]) == ("CNC", "LIMIT", "NSE", "TCS")
    assert o["price"] == pytest.approx(1005.0) and o["quantity"] == 5
    assert fill[0] == 1004.0 and fill[3] == 5 and fill[2] == "o1"


def test_unknown_outcome_is_never_resent():
    k = Kite(fail_place=True)
    assert B.KiteBroker(kite=k).execute(Order("BUY", "TCS.NS", 5, 1000.0, "t")) is None
    assert len(k.placed) == 1


def test_unknown_outcome_recovers_the_order_if_it_was_placed():
    k = Kite(fail_place=True, placed_anyway=True)
    fill = B.KiteBroker(kite=k).execute(Order("BUY", "TCS.NS", 5, 1000.0, "t"))
    assert fill and fill[2] == "o9" and len(k.placed) == 1


def test_refuses_to_sell_more_than_zerodha_holds():
    k = Kite(held=3)
    assert B.KiteBroker(kite=k).execute(Order("SELL", "TCS.NS", 5, 1000.0, "t")) is None
    assert not k.placed


def test_unfilled_remainder_is_cancelled_and_partial_fill_recorded():
    k = Kite(fill="OPEN", filled=2)
    fill = B.KiteBroker(kite=k).execute(Order("BUY", "TCS.NS", 5, 1000.0, "t"))
    assert k.cancelled == ["o1"] and fill[3] == 2


def test_reuses_the_shared_token_and_shares_a_fresh_one(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    os.makedirs(settings.shared_dir, exist_ok=True)
    path = os.path.join(settings.shared_dir, "kite_access_token.txt")
    with open(path, "w") as fh:
        fh.write("good")
    k = Kite()
    b = B.KiteBroker(kite=k)
    monkeypatch.setattr(b, "_login", lambda: pytest.fail("must reuse the valid shared token"))
    assert b.ready() and k.token == "good"

    with open(path, "w") as fh:
        fh.write("expired")
    k2 = Kite()
    b2 = B.KiteBroker(kite=k2)
    monkeypatch.setattr(b2, "_login", lambda: "good")
    assert b2.ready()
    assert open(path).read() == "good"           # the intraday agent picks it up too
