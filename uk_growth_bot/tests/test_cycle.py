"""End-to-end paper cycles on synthetic prices (no network)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from uk_growth_bot import main, ml_model
from uk_growth_bot import universe as U
from uk_growth_bot.config import settings
from uk_growth_bot.ledger import Ledger


@pytest.fixture
def closes():
    rng = np.random.default_rng(7)
    idx = pd.bdate_range(end="2027-10-29", periods=1100)
    data = {}
    for i, t in enumerate(U.ALL):
        drift = 0.0002 + 0.0001 * (i % 5)
        data[t] = 20 * np.exp(np.cumsum(rng.normal(drift, 0.012, len(idx))))
    return pd.DataFrame(data, index=idx)


@pytest.fixture
def env(tmp_path, monkeypatch, closes):
    monkeypatch.setattr(ml_model, "META_PATH", str(tmp_path / "m.json"))
    monkeypatch.setattr(ml_model, "MODEL_PATH", str(tmp_path / "m.joblib"))
    monkeypatch.setattr(main.market_data, "history",
                        lambda tickers, period="2y": closes[closes.index <= pd.Timestamp(main._today)])
    monkeypatch.setattr(main.market_data, "dividends_per_share", lambda t, since=None: pd.Series(dtype=float))
    monkeypatch.setattr("uk_growth_bot.research.sentiment",
                        lambda t, n: {"score": 0.0, "n": 0, "headlines": []})
    return Ledger(path=str(tmp_path / "l.db"), mode="paper")


def test_twelve_months_of_paper_investing(env, closes, monkeypatch):
    monkeypatch.setattr(settings, "regime_action", "ignore")  # pause is covered in test_planner
    ledger = env
    days = [d.date() for d in pd.bdate_range("2026-11-02", "2027-10-29") if d.day <= 7]
    run_days = sorted({min(d for d in days if (d.year, d.month) == ym)
                       for ym in {(d.year, d.month) for d in days}})
    for d in run_days:
        main._today = d
        main.run_cycle(d, ledger)
        assert ledger.cash() >= -0.01
        assert all(q > 0 for q in ledger.holdings().values())

    assert ledger.net_contributions() == pytest.approx(12 * 200)
    txns = ledger.txns()
    assert sum(t.side == "BUY" for t in txns) >= 10
    assert all(t.fees >= 3.0 for t in txns)
    nav = ledger.nav_history()[-1]
    assert nav["nav"] > 0 and nav["cash"] < 200 + 1   # money is invested, not idling
    held_slots = {U.core_slot(t) or t for t in ledger.holdings()}
    assert "VWRP.L" in held_slots


def test_rerunning_a_day_does_not_double_book_contributions(env):
    main._today = date(2027, 3, 1)
    main.run_cycle(main._today, env)
    main.run_cycle(main._today, env)
    assert env.net_contributions() == pytest.approx(200)


def test_ml_on_random_walks_fails_quality_gate(env, closes):
    rep = ml_model.train(closes)
    assert rep is not None and rep.test_samples > 0
    assert not rep.active          # noise must not be allowed to steer money
    assert ml_model.predict_scores(pd.DataFrame()) == {}


def test_weekly_report_renders(env, closes, monkeypatch):
    from uk_growth_bot import report
    from uk_growth_bot.research import run_research
    from uk_growth_bot.tax import tax_position

    for var in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    for d in (date(2027, 8, 2), date(2027, 9, 1)):
        main._today = d
        main.run_cycle(d, env)
    today = date(2027, 9, 10)
    main._today = today
    c = closes[closes.index <= pd.Timestamp(today)]
    rep = report.build(today, env, main.market_data.latest_prices(c), env.get_state("targets", {}),
                       run_research(c, env.holdings()), tax_position(env.txns(), [], today), None, c)
    for section in ("HOLDINGS", "ACTIVITY THIS WEEK", "RESEARCH", "UK TAX", "OUTLOOK"):
        assert section in rep["text"]
    assert "£" in rep["subject"] and "<pre" in rep["html"]
    assert report.send(rep) is False   # no credentials -> skipped, never raises
