from datetime import date

import pandas as pd
import pytest

from uk_growth_bot import universe as U
from uk_growth_bot.config import settings
from uk_growth_bot.planner import Planner, Research, compose, fees
from uk_growth_bot.tax import Txn, tax_position

TODAY = date(2026, 10, 5)


def research(scores=None, risk_on=True, overrides=None):
    base = {"mom_1m": 0.01, "mom_3m": 0.05, "mom_6m": 0.10, "mom_12m": 0.15, "vol_3m": 0.2,
            "dist_sma200": 0.05, "drawdown_1y": -0.05, "rsi_14": 55.0}
    feats = pd.DataFrame({t: dict(base) for t in U.ALL}).T
    for t, kv in (overrides or {}).items():
        for k, v in kv.items():
            feats.loc[t, k] = v
    sc = {t: 0.0 for t in U.ALL}
    sc.update(scores or {})
    return Research(feats, sc, risk_on=risk_on)


PRICES = {t: 10.0 for t in U.ALL}


def plan(cash=200.0, holdings=None, txns=None, held_since=None, r=None, today=TODAY, **kw):
    txns = txns or []
    p = Planner(today, r or research(), holdings or {}, PRICES, cash, txns,
                tax_position(txns, [], today), held_since or {}, **kw)
    return p.run()


def test_targets_sum_to_one_and_satellite_is_capped():
    out = plan(r=research({"AZN.L": 0.9, "REL.L": 0.8, "HLMA.L": 0.7}))
    assert sum(out.targets.values()) == pytest.approx(1.0)
    stocks = [t for t in out.targets if U.ALL[t].kind == "stock"]
    assert stocks == ["AZN.L", "REL.L"]
    assert sum(out.targets[s] for s in stocks) == pytest.approx(settings.satellite_pct)


def test_tilt_moves_core_weights_toward_stronger_signal():
    out = plan(r=research({"CNX1.L": 1.0, "VUAG.L": -1.0}))
    assert out.targets["CNX1.L"] > out.targets["VUAG.L"]


def test_monthly_contribution_buys_one_underweight_slot():
    out = plan(cash=200.0)
    assert len(out.orders) == 1
    o = out.orders[0]
    assert o.side == "BUY" and o.ticker == "VWRP.L"
    assert o.value + fees(o.ticker, "BUY", o.value) <= 200 - settings.cash_reserve


def test_small_cash_is_carried_forward():
    assert plan(cash=60.0).orders == []


def test_bear_regime_pauses_buying_then_resumes():
    out = plan(r=research(risk_on=False))
    assert out.orders == [] and out.paused_since == TODAY
    later = plan(r=research(risk_on=False), paused_since=date(2026, 6, 1))
    assert later.orders and any("Pause limit" in n for n in later.notes)


def test_negative_news_vetoes_satellite_entry():
    r = research({"AZN.L": 0.9})
    r.sentiment["AZN.L"] = -0.8
    out = plan(r=r)
    assert "AZN.L" not in out.targets


def test_thesis_broken_stock_is_sold_even_inside_min_hold():
    txns = [Txn(date(2026, 8, 1), "RR.L", "BUY", 10, 15.0)]
    r = research(overrides={"RR.L": {"drawdown_1y": -0.5, "dist_sma200": -0.2}})
    out = plan(holdings={"RR.L": 10}, txns=txns, held_since={"RR.L": date(2026, 8, 1)}, r=r)
    assert any(o.side == "SELL" and o.ticker == "RR.L" for o in out.orders)


def test_rotation_sale_deferred_when_gain_exceeds_allowance():
    txns = [Txn(date(2025, 1, 2), "RR.L", "BUY", 1000, 1.0)]  # £9k unrealised gain at £10
    r = research({"RR.L": -0.9, **{a.ticker: 0.5 for a in U.SATELLITE_CANDIDATES if a.ticker != "RR.L"}})
    out = plan(holdings={"RR.L": 1000}, txns=txns, held_since={"RR.L": date(2025, 1, 2)}, r=r)
    assert not any(o.side == "SELL" for o in out.orders)
    assert any("Deferred selling RR.L" in n for n in out.notes)


def test_rebuy_blocked_within_30_days_uses_twin():
    txns = [Txn(date(2026, 9, 1), "VWRP.L", "BUY", 10, 9.0), Txn(date(2026, 9, 20), "VWRP.L", "SELL", 10, 10.0)]
    out = plan(txns=txns)
    assert [o.ticker for o in out.orders] == ["FWRG.L"]


def test_harvest_switches_into_twin_within_allowance():
    txns = [Txn(date(2024, 1, 2), "VWRP.L", "BUY", 1000, 5.0)]  # £5/share gain
    out = plan(cash=0, holdings={"VWRP.L": 1000}, txns=txns, held_since={"VWRP.L": date(2024, 1, 2)},
               today=date(2027, 3, 1))
    sell = next(o for o in out.orders if o.side == "SELL")
    buy = next(o for o in out.orders if o.side == "BUY")
    assert sell.ticker == "VWRP.L" and buy.ticker == "FWRG.L"
    assert sell.quantity * 5.0 <= 3000 + 1e-6
    assert sell.quantity >= 590


def test_quarterly_rebalance_trims_overweight_slot():
    txns = [Txn(date(2026, 9, 1), "CNX1.L", "BUY", 100, 10.0)]
    out = plan(cash=0, holdings={"CNX1.L": 100}, txns=txns, held_since={"CNX1.L": date(2026, 9, 1)},
               rebalance_due=True)
    assert any(o.side == "SELL" and o.ticker == "CNX1.L" for o in out.orders)
    assert any(o.side == "BUY" for o in out.orders)


def test_stamp_duty_only_on_uk_share_purchases():
    assert fees("VWRP.L", "BUY", 1000) == pytest.approx(3.0)
    assert fees("AZN.L", "BUY", 1000) == pytest.approx(8.0)
    assert fees("AZN.L", "SELL", 1000) == pytest.approx(3.0)


def test_compose_renormalises_missing_signals():
    assert compose(0.5, None, None) == pytest.approx(0.5)
    assert compose(1.0, -1.0, None) == pytest.approx((0.6 - 0.25) / 0.85)
