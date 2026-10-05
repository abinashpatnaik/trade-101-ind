"""Trading 212 connector against a fake API shaped like the published OpenAPI spec."""

from datetime import date

import pytest
import requests

from uk_growth_bot import broker as B
from uk_growth_bot.planner import Order

INSTRUMENTS = [
    {"ticker": "VWRPl_EQ", "shortName": "VWRP", "currencyCode": "GBP", "type": "ETF"},
    {"ticker": "AZNl_EQ", "shortName": "AZN", "currencyCode": "GBX", "type": "STOCK"},
    {"ticker": "AZN_US_EQ", "shortName": "AZN", "currencyCode": "USD", "type": "STOCK"},
]


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body

    @property
    def text(self):
        return str(self._body)


class FakeT212:
    def __init__(self):
        self.calls = []
        self.auth = None
        self.headers = {}
        self.order_responses = []   # queued responses for POST /equity/orders/market
        self.pending = {}           # id -> polls left before it leaves the pending list
        self.history = []
        self.positions = []

    def request(self, method, url, timeout=None, json=None, **kw):
        path = url.split("/api/v0", 1)[1]
        self.calls.append((method, path, json))
        if path == "/equity/metadata/instruments":
            return Resp(200, INSTRUMENTS)
        if path == "/equity/account/summary":
            return Resp(200, {"cash": {"availableToTrade": 123.45}})
        if path == "/equity/positions":
            return Resp(200, self.positions)
        if method == "POST" and path == "/equity/orders/market":
            r = self.order_responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        if method == "GET" and path.startswith("/equity/orders/"):
            oid = int(path.rsplit("/", 1)[1])
            if self.pending.get(oid, 0) > 0:
                self.pending[oid] -= 1
                return Resp(200, {"id": oid, "status": "NEW"})
            return Resp(404, {"code": "NotFound"})
        if path == "/equity/orders":
            return Resp(200, [])
        if path.startswith("/equity/history/orders"):
            return Resp(200, {"items": self.history, "nextPagePath": None})
        if path.startswith("/equity/history/transactions"):
            return Resp(200, {"items": [
                {"type": "DEPOSIT", "amount": 200, "dateTime": "2026-10-01T08:00:00Z", "reference": "d1"},
                {"type": "WITHDRAW", "amount": 50, "dateTime": "2026-10-02T08:00:00Z", "reference": "w1"},
                {"type": "FEE", "amount": 0.5, "dateTime": "2026-10-02T08:00:00Z", "reference": "f1"},
            ], "nextPagePath": None})
        if path.startswith("/equity/history/dividends"):
            return Resp(200, {"items": [{"amount": 1.25, "paidOn": "2026-10-03T00:00:00Z",
                                         "reference": "v1", "ticker": "AZNl_EQ"}], "nextPagePath": None})
        return Resp(404)


def filled(oid, ticker, qty, price, taxes=()):
    return {"order": {"id": oid, "ticker": ticker, "status": "FILLED", "filledQuantity": qty},
            "fill": {"price": price, "quantity": qty,
                     "walletImpact": {"taxes": [{"name": n, "quantity": q} for n, q in taxes]}}}


@pytest.fixture
def t212(tmp_path, monkeypatch):
    monkeypatch.setattr(B.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(B.settings, "t212_api_key", "key")
    monkeypatch.setattr(B.settings, "t212_api_secret", "secret")
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    fake = FakeT212()
    return B.Trading212Broker(session=fake), fake


def test_uses_basic_auth_and_demo_by_default(t212):
    b, fake = t212
    assert fake.auth == ("key", "secret")
    assert b.base == "https://demo.trading212.com/api/v0"
    assert b.ready() and b.cash() == pytest.approx(123.45)


def test_instrument_map_prefers_london_line_and_honours_overrides(t212, monkeypatch):
    b, _ = t212
    assert b.instrument_map()["AZN.L"] == "AZNl_EQ"
    monkeypatch.setenv("UK_T212_TICKERS", "VUAG.L=VUAGl_EQ")
    b._map = None
    assert b.instrument_map()["VUAG.L"] == "VUAGl_EQ"


def test_buy_waits_for_fill_and_converts_pence(t212):
    b, fake = t212
    fake.order_responses = [Resp(200, {"id": 7, "status": "NEW"})]
    fake.pending[7] = 2
    fake.history = [filled(7, "AZNl_EQ", 0.75, 12000.0, [("STAMP_DUTY", 0.45)])]
    price, fee, oid, qty = b.execute(Order("BUY", "AZN.L", 0.75, 120.0, "test"))
    assert (price, fee, oid, qty) == (120.0, 0.45, "7", 0.75)
    post = [c for c in fake.calls if c[0] == "POST"][0]
    assert post[2] == {"ticker": "AZNl_EQ", "quantity": 0.75, "extendedHours": False}


def test_sell_sends_negative_quantity_and_refuses_more_than_held(t212):
    b, fake = t212
    fake.positions = [{"instrument": {"ticker": "VWRPl_EQ"}, "quantity": 2.5}]
    assert b.execute(Order("SELL", "VWRP.L", 3.0, 100.0, "test")) is None
    fake.order_responses = [Resp(200, {"id": 9})]
    fake.history = [filled(9, "VWRPl_EQ", 2.5, 101.0)]
    assert b.execute(Order("SELL", "VWRP.L", 2.5, 100.0, "test"))[3] == 2.5
    assert [c[2]["quantity"] for c in fake.calls if c[0] == "POST"] == [-2.5]


def test_unknown_outcome_is_never_resent(t212):
    b, fake = t212
    fake.order_responses = [requests.ConnectionError("dropped")]
    assert b.execute(Order("BUY", "VWRP.L", 1.0, 100.0, "test")) is None
    assert sum(1 for c in fake.calls if c[0] == "POST") == 1


def test_unknown_outcome_recovers_the_order_if_it_was_placed(t212):
    b, fake = t212
    fake.order_responses = [requests.Timeout("slow")]
    order_row = {"id": 11, "ticker": "VWRPl_EQ", "quantity": 1.0, "side": "BUY",
                 "initiatedFrom": "API", "createdAt": "2099-01-01T00:00:00Z", "status": "FILLED",
                 "filledQuantity": 1.0}
    fake.history = [{"order": order_row, "fill": {"price": 100.5, "quantity": 1.0}}]
    assert b.execute(Order("BUY", "VWRP.L", 1.0, 100.0, "test"))[2] == "11"
    assert sum(1 for c in fake.calls if c[0] == "POST") == 1


def test_precision_rejection_retries_with_fewer_decimals(t212):
    b, fake = t212
    fake.order_responses = [Resp(400, {"code": "QuantityPrecisionMismatch"}), Resp(200, {"id": 5})]
    fake.history = [filled(5, "VWRPl_EQ", 1.2, 100.0)]
    assert b.execute(Order("BUY", "VWRP.L", 1.27, 100.0, "test"))[3] == 1.2
    assert [c[2]["quantity"] for c in fake.calls if c[0] == "POST"] == [1.27, 1.2]


def test_rate_limited_order_is_retried_once(t212):
    b, fake = t212
    fake.order_responses = [Resp(429, None, {"x-ratelimit-reset": "0"}), Resp(200, {"id": 6})]
    fake.history = [filled(6, "VWRPl_EQ", 1.0, 100.0)]
    assert b.execute(Order("BUY", "VWRP.L", 1.0, 100.0, "test")) is not None


def test_cash_flows_classify_deposits_withdrawals_fees_dividends(t212):
    b, _ = t212
    flows = {ref: (d, a, k) for d, a, k, ref in b.cash_flows()}
    assert flows["t212:d1"] == (date(2026, 10, 1), 200.0, "contribution")
    assert flows["t212:w1"][1:] == (-50.0, "withdrawal")
    assert flows["t212:f1"][1:] == (-0.5, "fee")
    assert flows["t212:v1"][1:] == (1.25, "dividend")


def test_check_command_is_read_only(t212, monkeypatch, capsys):
    from uk_growth_bot import main
    b, fake = t212
    monkeypatch.setattr(main, "Trading212Broker", lambda: b)
    monkeypatch.setattr(B.settings, "mode", "live")       # even in live mode
    assert main.check() == 0
    out = capsys.readouterr().out
    assert "PRACTICE" in out and "OK   VWRP.L" in out and "MISS" in out and "no orders" in out
    assert not [c for c in fake.calls if c[0] != "GET"]


def test_check_fails_cleanly_without_key(monkeypatch, capsys):
    from uk_growth_bot import main
    monkeypatch.setattr(B.settings, "t212_api_key", "")
    assert main.check() == 1


def test_unknown_command_does_not_start_the_bot(monkeypatch):
    from uk_growth_bot import main
    monkeypatch.setattr(main, "loop", lambda: pytest.fail("loop must not start"))
    with pytest.raises(SystemExit) as e:
        main.main(["x", "chek"])
    assert e.value.code == 2


def test_429_with_stale_reset_header_waits_a_full_period(t212, monkeypatch):
    b, fake = t212
    slept = []
    monkeypatch.setattr(B.time, "sleep", lambda s: slept.append(s))
    stale = {"x-ratelimit-remaining": "0", "x-ratelimit-period": "5", "x-ratelimit-reset": "1"}
    responses = [Resp(200, {"cash": {"availableToTrade": 1}}, stale),
                 Resp(429, None, stale), Resp(200, {"cash": {"availableToTrade": 123.45}})]
    monkeypatch.setattr(fake, "request", lambda *a, **k: responses.pop(0))
    assert b.ready()
    assert b.cash() == pytest.approx(123.45)       # previously came back as None -> "£0.00"
    assert slept and all(s >= 4.9 for s in slept)  # waited out the 5s window, not the stale reset


def test_check_suggests_tickers_and_never_shows_fake_zero(t212, monkeypatch, capsys):
    from uk_growth_bot import main
    b, fake = t212
    monkeypatch.setattr(main, "Trading212Broker", lambda: b)
    monkeypatch.setattr(b, "cash", lambda: None)
    INSTRUMENTS.append({"ticker": "HLMAX_EQ", "shortName": "HLMX", "name": "Halma plc",
                        "currencyCode": "GBX"})
    try:
        assert main.check() == 0
    finally:
        INSTRUMENTS.pop()
    out = capsys.readouterr().out
    assert "unknown" in out and "£0.00" not in out
    assert "HLMAX_EQ (Halma plc, GBX)" in out


def test_precision_steps_down_one_decimal_at_a_time(t212, monkeypatch):
    b, fake = t212
    monkeypatch.setattr(B.settings, "qty_decimals", 4)
    reject = Resp(400, {"code": "QuantityPrecisionMismatch"})
    fake.order_responses = [reject, reject, Resp(200, {"id": 8})]
    fake.history = [filled(8, "VWRPl_EQ", 0.01, 1348.0)]
    assert b.execute(Order("BUY", "VWRP.L", 0.0142, 1348.0, "test"))[3] == 0.01
    assert [c[2]["quantity"] for c in fake.calls if c[0] == "POST"] == [0.0142, 0.014, 0.01]
