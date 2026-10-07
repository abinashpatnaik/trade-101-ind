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
    return Ledger(path=str(tmp_path / "l.db"), mode="sim")


def _run_year(ledger):
    days = [d.date() for d in pd.bdate_range("2026-11-02", "2027-10-29") if d.day <= 7]
    run_days = sorted({min(d for d in days if (d.year, d.month) == ym)
                       for ym in {(d.year, d.month) for d in days}})
    for d in run_days:
        main._today = d
        main.run_cycle(d, ledger)
        assert ledger.cash() >= -0.01
        assert all(q > 0 for q in ledger.holdings().values())
    assert ledger.net_contributions() == pytest.approx(12 * 200)
    core = {U.core_slot(t) for t in ledger.holdings() if U.core_slot(t)}
    assert core, "holds at least one core fund"
    groups = [U.get(s).group for s in core]
    assert len(groups) == len(set(groups)), f"two core funds from one group: {core}"
    return ledger.txns(), ledger.nav_history()[-1]


def test_twelve_months_isa_on_trading212(env, monkeypatch):
    monkeypatch.setattr(settings, "regime_action", "ignore")  # pause is covered in test_planner
    txns, nav = _run_year(env)
    etf_fees = [t.fees for t in txns if U.ALL[t.symbol].kind != "stock"]
    assert etf_fees and all(f == 0 for f in etf_fees)
    assert any(t.quantity != int(t.quantity) for t in txns)   # fractional shares
    assert nav["cash"] < 5                                     # every pound invested


@pytest.mark.usefixtures("gia_ibkr")
def test_twelve_months_gia_on_ibkr(env, monkeypatch):
    monkeypatch.setattr(settings, "regime_action", "ignore")
    txns, nav = _run_year(env)
    assert sum(t.side == "BUY" for t in txns) >= 10
    assert all(t.fees >= 3.0 for t in txns)
    assert all(t.quantity == int(t.quantity) for t in txns)
    assert nav["cash"] < 200 + 1


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
    assert "Stocks & Shares ISA" in rep["text"] and "of £20,000.00" in rep["text"]
    assert "£" in rep["subject"] and "<pre" in rep["html"]
    assert report.send(rep) is False   # no credentials -> skipped, never raises


@pytest.mark.parametrize("mode,env_name", [("live", "live"), ("live", "demo"), ("paper", "demo")])
def test_live_cycle_against_fake_trading212(env, closes, monkeypatch, tmp_path, mode, env_name):
    from uk_growth_bot import broker as B
    from uk_growth_bot.tests.test_trading212 import FakeT212, Resp

    today = date(2027, 3, 1)
    last = closes[closes.index <= pd.Timestamp(today)].iloc[-1]

    class AutoFill(FakeT212):
        next_id = 100

        def request(self, method, url, timeout=None, json=None, **kw):
            path = url.split("/api/v0", 1)[1]
            if path == "/equity/metadata/instruments":
                return Resp(200, [{"ticker": f"{t.split('.')[0]}l_EQ", "shortName": t.split(".")[0],
                                   "currencyCode": "GBP"} for t in U.ALL])
            if method == "POST" and path == "/equity/orders/market":
                self.calls.append((method, path, json))
                AutoFill.next_id += 1
                ours = json["ticker"].replace("l_EQ", ".L")
                self.history.append({"order": {"id": AutoFill.next_id, "status": "FILLED"},
                                     "fill": {"price": float(last[ours]) * 100,  # pence, like London lines
                                              "quantity": abs(json["quantity"])}})
                return Resp(200, {"id": AutoFill.next_id})
            return super().request(method, url, timeout=timeout, json=json, **kw)

    fake = AutoFill()
    monkeypatch.setattr(settings, "mode", mode)
    monkeypatch.setattr(settings, "t212_env", env_name)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    monkeypatch.setattr(main, "make_broker", lambda: B.Trading212Broker(session=fake))
    main._today = today
    main.run_cycle(today, env)

    buys = [c[2] for c in fake.calls if c[0] == "POST"]
    assert buys and all(b["quantity"] > 0 for b in buys)
    spent = sum(t.quantity * t.price for t in env.txns())
    if env_name == "live":
        assert env.net_contributions() == pytest.approx(150.0)    # +200 deposit, -50 withdrawal
        assert len(env.dividends()) == 1
        assert spent <= 123.45 + 0.01                               # capped at broker's available cash
    else:
        # practice account: pretend £200/month, regardless of its virtual balance
        assert env.net_contributions() == pytest.approx(200.0)
        assert not any(c[1].startswith("/equity/history/transactions") for c in fake.calls)
        assert spent <= 123.45 + 0.01
    for t in env.txns():
        assert t.price == pytest.approx(float(last[t.symbol]), abs=1e-4)   # pence converted to £
    notes = " ".join(d["text"] for d in env.decisions(today))
    assert ("Trading 212 contribution" if env_name == "live" else "Monthly contribution") in notes


def test_first_live_run_books_existing_isa_cash_as_opening_balance(env, monkeypatch):
    class Broker:
        def cash_flows(self):
            return [(date(2027, 2, 1), 200.0, "contribution", "t212:d1")]

        def cash(self):
            return 950.0

    monkeypatch.setattr(settings, "mode", "live")
    monkeypatch.setattr(settings, "t212_env", "live")
    main._record_contributions(env, Broker(), date(2027, 3, 1))
    assert env.net_contributions() == pytest.approx(950.0)          # 200 deposit + 750 opening
    main._record_contributions(env, Broker(), date(2027, 3, 2))
    assert env.net_contributions() == pytest.approx(950.0)          # only once


def test_live_cycle_buys_nothing_when_broker_cash_is_unknown(env, closes, monkeypatch):
    from uk_growth_bot.broker import SimBroker

    class Blind(SimBroker):
        def ready(self):
            return True

        def cash(self):
            return None

        def execute(self, order):
            pytest.fail("must not trade without a known balance")

    monkeypatch.setattr(settings, "mode", "live")
    monkeypatch.setattr(settings, "t212_env", "demo")
    monkeypatch.setattr(main, "make_broker", Blind)
    main._today = date(2027, 3, 1)
    main.run_cycle(main._today, env)
    assert not env.txns()
    assert any("no purchases today" in d["text"] for d in env.decisions(main._today))


def test_dry_run_sends_no_orders_and_leaves_ledger_untouched(env, closes, monkeypatch, tmp_path, capsys):
    from uk_growth_bot import broker as B
    from uk_growth_bot.tests.test_trading212 import FakeT212, Resp

    class Fake(FakeT212):
        def request(self, method, url, timeout=None, json=None, **kw):
            if url.endswith("/equity/metadata/instruments"):
                self.calls.append((method, url, json))
                return Resp(200, [{"ticker": f"{t.split('.')[0]}l_EQ", "shortName": t.split(".")[0],
                                   "currencyCode": "GBP"} for t in U.ALL])
            return super().request(method, url, timeout=timeout, json=json, **kw)

    fake = Fake()
    monkeypatch.setattr(settings, "mode", "live")
    monkeypatch.setattr(settings, "t212_env", "demo")
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    monkeypatch.setattr(main, "make_broker", lambda: B.Trading212Broker(session=fake))
    main._today = date(2027, 3, 1)
    assert main.dry_run(main._today) == 0
    out = capsys.readouterr().out
    assert "WOULD BUY" in out and "No orders sent" in out
    assert not [c for c in fake.calls if c[0] == "POST"]
    real = main.Ledger()
    assert not real.txns() and real.net_contributions() == 0


def test_status_prints_summary(env, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    assert main.status() == 0
    out = capsys.readouterr().out
    assert "Mode sim" in out and "SIMULATION" in out and "Holdings: none" in out


@pytest.mark.parametrize("hhmm,last_run,expected", [
    ("10:29", None, False),          # too early
    ("10:30", None, True),
    ("15:59", None, True),
    ("16:00", None, False),          # after the window: orders would queue until tomorrow's open
    ("22:00", None, False),          # a container restarted in the evening must not trade
    ("11:00", "2027-03-01", False),  # already ran today
])
def test_cycle_only_runs_inside_trading_hours(monkeypatch, hhmm, last_run, expected):
    from datetime import datetime
    monkeypatch.setattr(main, "_lse_open", lambda d: True)
    h, m = map(int, hhmm.split(":"))
    assert main._cycle_due(datetime(2027, 3, 1, h, m, tzinfo=main.TZ), last_run) is expected


def test_cycle_never_runs_on_a_closed_day(monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(main, "_lse_open", lambda d: False)
    assert main._cycle_due(datetime(2027, 3, 1, 11, 0, tzinfo=main.TZ), None) is False


def test_failed_cycle_is_not_retried_the_same_day(env, monkeypatch):
    from datetime import datetime
    calls = []

    def boom(today, ledger, broker=None):
        calls.append(today)
        raise RuntimeError("broker exploded mid-cycle")

    monkeypatch.setattr(main, "run_cycle", boom)
    monkeypatch.setattr(main, "_lse_open", lambda d: True)
    monkeypatch.setattr(main, "Ledger", lambda: env)
    monkeypatch.setattr(main.ml_model, "load_report", lambda: object())
    now = datetime(2027, 3, 1, 11, 0, tzinfo=main.TZ)

    class Clock:
        @staticmethod
        def now(tz=None):
            return now

    monkeypatch.setattr(main, "datetime", Clock)
    ticks = iter(range(3))
    monkeypatch.setattr(main.time, "sleep", lambda s: next(ticks))
    with pytest.raises(StopIteration):
        main.loop()                  # three scheduler ticks at 11:00 on the same day
    assert len(calls) == 1


def test_report_command_exit_code_reflects_email(env, closes, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(main, "Ledger", lambda: env)
    main._today = date(2027, 3, 1)
    monkeypatch.setattr(main.report, "send", lambda rep: True)
    with pytest.raises(SystemExit) as e:
        main.main(["x", "report"])
    assert e.value.code == 0
    monkeypatch.setattr(main.report, "send", lambda rep: False)
    with pytest.raises(SystemExit) as e:
        main.main(["x", "report"])
    assert e.value.code == 1
    assert list((tmp_path / "reports").glob("weekly_*.txt"))


def test_funds_not_in_gbp_are_never_bought(env, monkeypatch):
    monkeypatch.setattr(settings, "regime_action", "ignore")
    monkeypatch.setattr(main.market_data, "non_gbp", lambda tickers: {"SMGB.L", "EMIM.L"} & set(tickers))
    for d in (date(2027, 3, 1), date(2027, 4, 1)):
        main._today = d
        research, plan = main.run_cycle(d, env)
        assert not {"SMGB.L", "EMIM.L"} & set(research.scores)
    assert env.txns() and not {"SMGB.L", "EMIM.L"} & {t.symbol for t in env.txns()}


def test_funds_missing_on_trading212_are_skipped_unless_held(monkeypatch):
    class Broker:
        def instrument_map(self):
            return {t: t for t in U.ALL if t not in ("WLDS.L", "IWQU.L")}

    monkeypatch.setattr(settings, "mode", "paper")
    monkeypatch.setattr(main.market_data, "non_gbp", lambda tickers: set())
    assert main._untradable(Broker(), list(U.ALL), {"IWQU.L": 1.0}) == {"WLDS.L"}
