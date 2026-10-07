from datetime import date

import pandas as pd
import pytest

from in_growth_bot import universe as U
from in_growth_bot.config import settings
from in_growth_bot.planner import Planner, Research, fees, round_qty
from in_growth_bot.tax import Txn, tax_position

TODAY = date(2026, 10, 5)
POOL = [a.ticker for a in U.pool()]


def research(scores=None, risk_on=True, overrides=None, sentiment=None):
    base = {"mom_1m": 0.01, "mom_3m": 0.05, "mom_6m": 0.10, "mom_12m": 0.15, "vol_3m": 0.2,
            "dist_sma200": 0.05, "drawdown_1y": -0.05, "rsi_14": 55.0}
    feats = pd.DataFrame({t: dict(base) for t in POOL}).T
    for t, kv in (overrides or {}).items():
        for k, v in kv.items():
            feats.loc[t, k] = v
    sc = {t: -0.5 for t in POOL}
    sc.update(scores or {})
    return Research(feats, sc, sentiment=sentiment or {}, risk_on=risk_on)


PRICES = {t: 1000.0 for t in POOL}


def plan(cash=25000.0, holdings=None, txns=None, held_since=None, r=None, today=TODAY, prices=None, **kw):
    txns = txns or []
    p = Planner(today, r or research(), holdings or {}, prices or PRICES, cash, txns,
                tax_position(txns, today), held_since or {}, **kw)
    return p.run()


TOP = {"TCS.NS": 0.9, "INFY.NS": 0.85, "HCLTECH.NS": 0.8, "HDFCBANK.NS": 0.75, "ICICIBANK.NS": 0.7,
       "SBIN.NS": 0.65, "TITAN.NS": 0.6, "LT.NS": 0.55, "HAL.NS": 0.5, "SUNPHARMA.NS": 0.45}


def test_picks_top_n_with_at_most_two_per_sector():
    out = plan(r=research(TOP))
    assert set(out.targets) == {"TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS", "TITAN.NS", "LT.NS",
                                "HAL.NS", "SUNPHARMA.NS"}       # 3rd IT and 3rd bank skipped
    assert sum(out.targets.values()) == pytest.approx(1.0)
    assert out.targets["TCS.NS"] > out.targets["SUNPHARMA.NS"]


def test_buys_whole_shares_in_orders_of_at_least_ten_thousand():
    out = plan(r=research(TOP))
    buys = [o for o in out.orders if o.side == "BUY"]
    assert 1 <= len(buys) <= 2
    assert all(o.quantity == int(o.quantity) and o.value >= 9000 for o in buys)
    spent = sum(o.value + fees(o.ticker, "BUY", o.value) for o in buys)
    assert 25000 - settings.cash_reserve - 1100 <= spent <= 25000 - settings.cash_reserve


def test_expensive_share_is_still_bought_when_the_money_covers_one():
    prices = dict(PRICES, **{"TCS.NS": 14000.0})
    out = plan(r=research(TOP), prices=prices)
    assert any(o.ticker == "TCS.NS" and o.quantity == 1 for o in out.orders)


def test_nro_charges():
    # 0.5% brokerage capped at Rs50, STT 0.1% both sides, stamp on buys, DP on sells, GST.
    buy = fees("TCS.NS", "BUY", 25000)
    assert buy == pytest.approx(50 + 25 + 3.75 + 0.7425 + 0.025 + 0.18 * (50 + 0.7425 + 0.025), abs=0.02)
    assert fees("TCS.NS", "BUY", 2000) < fees("TCS.NS", "BUY", 25000)
    assert fees("TCS.NS", "SELL", 25000) > buy - 3.75   # DP charge on the sell side
    assert round_qty(3.99) == 3


def test_crash_rule_sells_at_a_loss():
    txns = [Txn(date(2026, 8, 1), "TCS.NS", "BUY", 20, 1500.0)]
    r = research(TOP, overrides={"TCS.NS": {"drawdown_1y": -0.40, "dist_sma200": -0.10}})
    out = plan(r=r, holdings={"TCS.NS": 20}, txns=txns, held_since={"TCS.NS": date(2026, 8, 1)})
    assert any(o.side == "SELL" and o.ticker == "TCS.NS" for o in out.orders)


def test_rotation_waits_a_year_then_sells_only_in_profit():
    low = dict(TOP, **{"DIVISLAB.NS": -0.95})
    young = [Txn(date(2026, 3, 1), "DIVISLAB.NS", "BUY", 10, 800.0)]
    out = plan(r=research(low), holdings={"DIVISLAB.NS": 10}, txns=young, held_since={"DIVISLAB.NS": date(2026, 3, 1)})
    assert not any(o.side == "SELL" for o in out.orders)            # held < 365 days
    old = [Txn(date(2025, 3, 1), "DIVISLAB.NS", "BUY", 10, 800.0)]
    out = plan(r=research(low), holdings={"DIVISLAB.NS": 10}, txns=old, held_since={"DIVISLAB.NS": date(2025, 3, 1)})
    assert any(o.side == "SELL" and o.ticker == "DIVISLAB.NS" for o in out.orders)
    under = [Txn(date(2025, 3, 1), "DIVISLAB.NS", "BUY", 10, 1200.0)]
    out = plan(r=research(low), holdings={"DIVISLAB.NS": 10}, txns=under, held_since={"DIVISLAB.NS": date(2025, 3, 1)})
    assert not any(o.side == "SELL" for o in out.orders)
    assert "DIVISLAB.NS" in out.held_at_loss
    assert not any(o.side == "BUY" and o.ticker == "DIVISLAB.NS" for o in out.orders)


def test_sale_with_big_short_term_tax_is_deferred():
    # Held a year overall, but most shares were added recently at a much lower price.
    low = dict(TOP, **{"DIVISLAB.NS": -0.95})
    txns = [Txn(date(2025, 3, 1), "DIVISLAB.NS", "BUY", 1, 900.0), Txn(date(2026, 6, 1), "DIVISLAB.NS", "BUY", 50, 500.0)]
    out = plan(r=research(low), holdings={"DIVISLAB.NS": 51}, txns=txns,
               held_since={"DIVISLAB.NS": date(2025, 3, 1)})
    assert not any(o.side == "SELL" for o in out.orders)
    assert any("Deferred selling DIVISLAB.NS" in n for n in out.notes)


def test_harvest_in_march_then_buy_back_next_run():
    txns = [Txn(date(2024, 6, 3), "TCS.NS", "BUY", 100, 500.0)]   # Rs50k long-term gain at Rs1,000
    today = date(2027, 3, 10)
    out = plan(cash=0, r=research(TOP), holdings={"TCS.NS": 100}, txns=txns,
               held_since={"TCS.NS": date(2024, 6, 3)}, today=today)
    sell = next(o for o in out.orders if o.side == "SELL")
    assert sell.ticker == "TCS.NS" and sell.quantity == 100 and "Tax harvest" in sell.reason
    assert not any(o.side == "BUY" for o in out.orders)            # proceeds wait for tomorrow
    assert out.rebuy == {"TCS.NS": 100}
    # Next run: the sale settled into cash; buy the same shares back first.
    nxt = plan(cash=99000, r=research(TOP), holdings={}, txns=txns + [Txn(today, "TCS.NS", "SELL", 100, 1000.0)],
               today=date(2027, 3, 11), rebuy={"TCS.NS": 100})
    rb = [o for o in nxt.orders if o.reason.startswith("Buying back")]
    assert rb and rb[0].ticker == "TCS.NS" and rb[0].quantity >= 98
    assert "TCS.NS" in nxt.targets
    assert not nxt.rebuy


def test_bear_market_pauses_new_money():
    out = plan(r=research(TOP, risk_on=False))
    assert not out.orders and out.paused_since == TODAY


def test_etfs_only_when_opted_in(monkeypatch):
    assert not any(U.ALL[t].kind == "etf" for t in plan(r=research(TOP)).targets)
    monkeypatch.setattr(settings, "include_etfs", True)
    pool = [a.ticker for a in U.pool()]
    assert "NIFTYBEES.NS" in pool
