"""End-to-end cycles on synthetic prices (no network)."""

import json
import os
from datetime import date

import numpy as np
import pandas as pd
import pytest

from in_growth_bot import main, ml_model
from in_growth_bot import universe as U
from in_growth_bot.config import settings
from in_growth_bot.ledger import Ledger


@pytest.fixture
def closes():
    rng = np.random.default_rng(11)
    idx = pd.bdate_range(end="2027-10-29", periods=1100)
    data = {}
    for i, t in enumerate(U.data_tickers()):
        start = 110.0 if t == U.FX else 300 + 150 * (i % 12)
        drift = 0.0 if t == U.FX else 0.0003 + 0.0001 * (i % 5)
        data[t] = start * np.exp(np.cumsum(rng.normal(drift, 0.004 if t == U.FX else 0.014, len(idx))))
    return pd.DataFrame(data, index=idx)


@pytest.fixture
def env(tmp_path, monkeypatch, closes):
    monkeypatch.setattr(ml_model, "META_PATH", str(tmp_path / "m.json"))
    monkeypatch.setattr(ml_model, "MODEL_PATH", str(tmp_path / "m.joblib"))
    monkeypatch.setattr(main.market_data, "history",
                        lambda tickers, period="2y": closes[closes.index <= pd.Timestamp(main._today)])
    monkeypatch.setattr(main.market_data, "dividends_per_share", lambda t, since=None: pd.Series(dtype=float))
    monkeypatch.setattr("in_growth_bot.research.sentiment", lambda t, n: {"score": 0.0, "n": 0, "headlines": []})
    return Ledger(path=str(tmp_path / "l.db"), mode=settings.mode)


def _first_trading_days():
    days = [d.date() for d in pd.bdate_range("2026-11-02", "2027-10-29") if d.day <= 7]
    return sorted({min(d for d in days if (d.year, d.month) == ym) for ym in {(d.year, d.month) for d in days}})


def test_twelve_months_in_simulation(env, monkeypatch):
    monkeypatch.setattr(settings, "regime_action", "ignore")
    for d in _first_trading_days():
        main._today = d
        main.run_cycle(d, env)
        assert env.cash() >= -0.01
    assert env.net_contributions() == pytest.approx(12 * 25000)
    txns = env.txns()
    assert all(t.quantity == int(t.quantity) for t in txns)
    assert all(t.fx > 50 for t in txns)                               # GBP/INR captured per trade
    held = env.holdings()
    assert 4 <= len(held) <= settings.positions
    groups = [U.ALL[t].group for t in held]
    assert max(groups.count(g) for g in groups) <= settings.max_per_group
    assert env.nav_history()[-1]["cash"] < 25000                       # money gets invested
    assert not os.path.exists(os.path.join(settings.shared_dir, main.RESERVED_FILE))   # sim: no ring-fence


class FakeKite:
    """Minimal Kite Connect: fills every LIMIT order at its price."""

    def __init__(self, closes, cash=60000.0, extra_holdings=None):
        self.closes, self.cash_balance = closes, cash
        self.orders_placed, self.history = [], {}
        self.holdings_rows = [{"tradingsymbol": s, "exchange": "NSE", "quantity": q, "t1_quantity": 0}
                              for s, q in (extra_holdings or {}).items()]
        self.token = None

    def set_access_token(self, t):
        self.token = t

    def profile(self):
        if self.token != "good":
            raise Exception("TokenException")
        return {"user_id": "AB1234"}

    def margins(self, segment=None):
        return {"net": self.cash_balance}

    def holdings(self):
        return self.holdings_rows

    def positions(self):
        net = {}
        for o in self.orders_placed:
            q = o["quantity"] if o["transaction_type"] == "BUY" else -o["quantity"]
            net[o["tradingsymbol"]] = net.get(o["tradingsymbol"], 0) + q
        return {"net": [{"tradingsymbol": s, "exchange": "NSE", "product": "CNC", "quantity": q}
                        for s, q in net.items()]}

    def ltp(self, keys):
        keys = [keys] if isinstance(keys, str) else keys
        out = {}
        for k in keys:
            t = k.split(":", 1)[1] + ".NS"
            if t in self.closes:
                out[k] = {"last_price": float(self.closes[t][self.closes.index <= pd.Timestamp(main._today)].iloc[-1])}
        return out

    def place_order(self, **kw):
        oid = f"o{len(self.orders_placed) + 1}"
        self.orders_placed.append(kw)
        v = kw["quantity"] * kw["price"]
        self.cash_balance += -v if kw["transaction_type"] == "BUY" else v
        self.history[oid] = [{"status": "COMPLETE", "filled_quantity": kw["quantity"], "average_price": kw["price"]}]
        return oid

    def order_history(self, oid):
        return self.history[oid]

    def orders(self):
        return []


def test_live_cycle_on_kite_ring_fences_its_money_and_shares(env, closes, monkeypatch, tmp_path):
    from in_growth_bot import broker as B

    monkeypatch.setattr(settings, "mode", "live")
    monkeypatch.setattr(settings, "regime_action", "ignore")
    monkeypatch.setenv("KITE_API_KEY", "k")
    os.makedirs(settings.shared_dir, exist_ok=True)
    with open(os.path.join(settings.shared_dir, "kite_access_token.txt"), "w") as fh:
        fh.write("good")                                               # the intraday agent's token
    fake = FakeKite(closes, extra_holdings={"RELIANCE": 7})            # someone else's shares
    monkeypatch.setattr(main, "make_broker", lambda: B.KiteBroker(kite=fake))
    live = Ledger(path=str(tmp_path / "live.db"), mode="live")
    main._today = date(2027, 3, 1)
    main.run_cycle(main._today, live)

    assert fake.orders_placed and all(o["product"] == "CNC" and o["order_type"] == "LIMIT"
                                      and o["tag"] == B.ORDER_TAG for o in fake.orders_placed)
    assert live.net_contributions() == pytest.approx(25000)
    reserved = json.load(open(os.path.join(settings.shared_dir, main.RESERVED_FILE)))
    assert reserved["holdings"] == live.holdings() and "RELIANCE.NS" not in reserved["holdings"]
    assert reserved["cash"] == pytest.approx(max(0.0, live.cash()), abs=0.01)
    spent = sum(o["quantity"] * o["price"] for o in fake.orders_placed)
    assert spent <= 25000                                              # never touches the other Rs35k


def test_live_cycle_buys_nothing_until_the_transfer_lands(env, closes, monkeypatch, tmp_path):
    from in_growth_bot import broker as B

    monkeypatch.setattr(settings, "mode", "live")
    monkeypatch.setenv("KITE_API_KEY", "k")
    os.makedirs(settings.shared_dir, exist_ok=True)
    with open(os.path.join(settings.shared_dir, "kite_access_token.txt"), "w") as fh:
        fh.write("good")
    fake = FakeKite(closes, cash=300.0)
    monkeypatch.setattr(main, "make_broker", lambda: B.KiteBroker(kite=fake))
    live = Ledger(path=str(tmp_path / "live.db"), mode="live")
    main._today = date(2027, 3, 1)
    main.run_cycle(main._today, live)
    assert not fake.orders_placed
    assert any("has this month's transfer arrived" in d["text"] for d in live.decisions(main._today))


def test_weekly_report_renders(env, closes, monkeypatch):
    from in_growth_bot import report
    from in_growth_bot.research import run_research

    for var in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    for d in (date(2027, 8, 2), date(2027, 9, 1)):
        main._today = d
        main.run_cycle(d, env)
    today = date(2027, 9, 10)
    main._today = today
    c = closes[closes.index <= pd.Timestamp(today)]
    rep = report.build(today, env, main.market_data.latest_prices(c), env.get_state("targets", {}),
                       run_research(c, env.holdings()), None, c)
    for section in ("HOLDINGS", "ACTIVITY THIS WEEK", "RESEARCH", "INDIAN TAX", "UK TAX", "OUTLOOK"):
        assert section in rep["text"]
    assert "Rs" in rep["subject"] and "<pre" in rep["html"]
    assert report.send(rep) is False


def test_dry_run_leaves_ledger_and_ring_fence_alone(env, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(main, "Ledger", lambda path=None, mode=None: Ledger(path=path or str(tmp_path / "real.db"),
                                                                            mode=mode))
    main._today = date(2027, 3, 1)
    assert main.dry_run(main._today) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert not Ledger(path=str(tmp_path / "real.db")).txns()
