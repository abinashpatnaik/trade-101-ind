"""
Harness wiring: point-in-time discipline, the matched control, and clustering.

Uses a synthetic BarSource and an injected replay_fn so the whole pipeline runs
offline in milliseconds — no network, no ML model, no broker.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from research.harness import StudyResult, run_study
from research.signals import CATALOGUE, Momentum20, PullbackInUptrend


class FakeSource:
    """Deterministic bars. Daily history rises for 'UP*', falls for 'DOWN*'."""

    def __init__(self, symbols, days=400):
        self.symbols = list(symbols)
        self._daily = {}
        base = pd.Timestamp("2026-01-01")
        idx = pd.date_range(base - pd.Timedelta(days=days), periods=days, freq="B")
        for i, s in enumerate(self.symbols):
            drift = 0.001 if s.startswith("UP") else -0.001
            close = 100 * np.cumprod(1 + drift + 0.0001 * np.sin(np.arange(days) + i))
            self._daily[s] = pd.DataFrame(
                {"Open": close, "High": close * 1.01, "Low": close * 0.99,
                 "Close": close, "Volume": np.full(days, 5_000_000.0)}, index=idx)
        self.requested_5m = []

    def intraday_limit_days(self):
        return None

    def daily(self, symbols, years=2):
        return {s: d for s, d in self._daily.items() if s in symbols}

    def trading_calendar(self, reference, years=5):
        return [d.normalize() for d in self._daily[self.symbols[0]].index]

    def bars_5m(self, symbol, start, end):
        self.requested_5m.append((symbol, start, end))
        idx = pd.date_range(start, end, freq="5min")[:400]
        if len(idx) < 60:
            return None
        close = np.linspace(100, 101, len(idx))
        return pd.DataFrame({"Open": close, "High": close * 1.001,
                             "Low": close * 0.999, "Close": close,
                             "Volume": np.full(len(idx), 1000.0)}, index=idx)


def _fixed_replay(value=1.0, n=2):
    return lambda sym, bars: [value] * n


@pytest.fixture()
def symbols():
    return [f"UP{i}" for i in range(12)] + [f"DOWN{i}" for i in range(12)]


def _run(sig, symbols, **kw):
    src = FakeSource(symbols)
    kw.setdefault("n_windows", 3)
    kw.setdefault("top_n", 5)
    kw.setdefault("min_price", 1.0)
    kw.setdefault("min_volume", 1.0)
    return run_study(sig, symbols, market="IN", source=src,
                     replay_fn=_fixed_replay(), **kw), src


def test_study_runs_and_clusters_by_symbol_and_window(symbols):
    study, _ = _run(Momentum20(), symbols)
    assert study.n_trades > 0
    for key in study.selected:
        assert "|" in key, "cluster key must be symbol|window"
    tags = {k.split("|")[1] for k in study.selected}
    assert len(tags) > 1, "should span multiple windows"


def test_selection_only_sees_pre_cutoff_bars(symbols):
    """The signal must never be handed a bar at or after the cutoff."""
    seen = {}

    class Spy:
        name = "spy"

        def rank(self, histories, cutoff):
            for sym, df in histories.items():
                if not df.empty:
                    assert df.index[-1] < cutoff
            seen[cutoff] = len(histories)
            return {s: 1.0 for s in histories}

    study, _ = _run(Spy(), symbols)
    assert seen, "signal was never invoked"
    assert study.picks


def test_evaluation_bars_start_at_or_after_cutoff(symbols):
    _study, src = _run(Momentum20(), symbols)
    for _sym, start, end in src.requested_5m:
        assert start < end


def test_control_basket_is_built_and_matched_in_size(symbols):
    study, _ = _run(Momentum20(), symbols, top_n=5)
    assert study.control, "a matched random control is mandatory"
    for tag in study.picks:
        sel = [k for k in study.selected if k.endswith(f"|{tag}")]
        ctl = [k for k in study.control if k.endswith(f"|{tag}")]
        assert abs(len(sel) - len(ctl)) <= 1


def test_control_and_selection_never_overlap(symbols):
    study, _ = _run(Momentum20(), symbols)
    assert not (set(study.selected) & set(study.control))


def test_momentum_picks_the_risers(symbols):
    study, _ = _run(Momentum20(), symbols)
    picked = [s for picks in study.picks.values() for s in picks]
    assert sum(s.startswith("UP") for s in picked) > sum(s.startswith("DOWN") for s in picked)


def test_report_applies_guards_and_counts_variants(symbols):
    study, _ = _run(Momentum20(), symbols)
    rep = study.report(n_variants_tried=5)
    assert rep.n_trades == study.n_trades
    assert any("5 variants" in n for n in rep.notes)


def test_identical_baskets_do_not_pass(symbols):
    """Selection and control drawn from the same constant returns must FAIL —
    no separation means no signal, however positive the raw total."""
    study, _ = _run(Momentum20(), symbols)
    rep = study.report()
    assert rep.p_vs_random > 0.05
    assert rep.passed is False


def test_empty_rank_yields_no_picks(symbols):
    class Nothing:
        name = "nothing"

        def rank(self, histories, cutoff):
            return {}

    study, _ = _run(Nothing(), symbols)
    assert study.n_trades == 0
    assert all(p == [] for p in study.picks.values())


def test_intraday_limited_market_warns(symbols):
    class Limited(FakeSource):
        def intraday_limit_days(self):
            return 58

    src = Limited(symbols)
    study = run_study(Momentum20(), symbols, market="IN", source=src,
                      replay_fn=_fixed_replay(), n_windows=2, top_n=3,
                      min_price=1.0, min_volume=1.0)
    assert any("one market regime" in w for w in study.warnings)


def test_catalogue_signals_all_implement_rank(symbols):
    src = FakeSource(symbols)
    hist = {s: d.iloc[:-5] for s, d in src.daily(symbols).items()}
    cutoff = src.trading_calendar("x")[-1]
    for name, sig in CATALOGUE.items():
        scores = sig.rank(hist, cutoff)
        assert isinstance(scores, dict), f"{name} must return a dict"
        assert all(isinstance(v, float) for v in scores.values()), name


def test_pullback_rejects_extended_names(symbols):
    """A name at its highs must not qualify as a pullback."""
    src = FakeSource(symbols)
    hist = src.daily(symbols)
    picks = PullbackInUptrend().rank(hist, src.trading_calendar("x")[-1])
    for sym in picks:
        c = hist[sym]["Close"]
        assert c.iloc[-1] / c.iloc[-20:].max() - 1 <= -0.03


# --------------------------------------------------------------- edge study
def test_edge_study_reports_gross_separately(symbols):
    """Gross must be reported net + friction — it is the diagnostic that
    decides whether any allocation layer is worth building."""
    from research.edge_study import run_edge_study

    src = FakeSource(symbols)
    res = run_edge_study(symbols[:4], market="IN", n_windows=3, window_days=10,
                         source=src, replay_fn=lambda s, b: [(-0.1, "EOD")] * 3,
                         calendar_reference=symbols[0])
    s = res.summary()
    assert s["trades"] == 4 * 3 * 3
    # net is -0.1; gross must be higher by exactly the friction
    assert s["gross_avg"] == pytest.approx(-0.1 + res.friction_pct, abs=1e-9)
    assert "GROSS EDGE BY WINDOW" in res.render()


def test_edge_study_flags_ci_below_friction(symbols):
    from research.edge_study import run_edge_study

    src = FakeSource(symbols)
    res = run_edge_study(symbols[:4], market="IN", n_windows=4, window_days=10,
                         source=src, replay_fn=lambda s, b: [(-0.2, "EOD")] * 4,
                         calendar_reference=symbols[0])
    assert "BELOW friction" in res.render()


def test_edge_study_collects_exit_reasons(symbols):
    from research.edge_study import run_edge_study

    src = FakeSource(symbols)
    res = run_edge_study(symbols[:3], market="IN", n_windows=2, window_days=10,
                         source=src, calendar_reference=symbols[0],
                         replay_fn=lambda s, b: [(1.5, "TRAILING_STOP"), (-3.0, "STOP_LOSS")])
    assert set(res.exit_reasons) == {"TRAILING_STOP", "STOP_LOSS"}
    assert "TRAILING_STOP" in res.render()
