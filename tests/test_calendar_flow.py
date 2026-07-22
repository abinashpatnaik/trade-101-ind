"""
Calendar-flow battery.

Each test builds synthetic price data with a KNOWN property, so we can assert
the battery accepts a real effect and rejects each specific way of faking one.
No network, no market data.
"""

import numpy as np
import pandas as pd
import pytest

from research.calendar_flow import (
    MonthEndFlow, by_year, duration_gradient, parameter_sweep, plateau_fraction,
    rest_of_month_control, validate, walk_forward,
)

ERAS = [(2015, 2018), (2019, 2022), (2023, 2026)]


def _series(bump_at_month_end=0.0, drift=0.0, noise=0.0, seed=0,
            years=12, bump_years=None, bump_width=6):
    """Daily closes with an optional excess return in the last `bump_width`
    trading days of each month."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-01", periods=252 * years)
    df = pd.DataFrame(index=idx)
    df["month"] = idx.to_period("M")
    rets = np.full(len(idx), drift) + rng.normal(0, noise, len(idx))
    pos = 0
    for _m, grp in df.groupby("month", sort=True):
        n = len(grp)
        if n > bump_width:
            yr = grp.index[0].year
            if bump_years is None or yr in bump_years:
                rets[pos + n - bump_width: pos + n] += bump_at_month_end
        pos += n
    close = 100 * np.cumprod(1 + rets)
    return pd.DataFrame({"Close": close, "Open": close, "High": close,
                         "Low": close, "Volume": 1e6}, index=idx)


# ------------------------------------------------------------------ basics
def test_entry_must_precede_exit():
    with pytest.raises(ValueError):
        MonthEndFlow(entry_days_before_end=2, exit_days_before_end=5)
    with pytest.raises(ValueError):
        MonthEndFlow(entry_days_before_end=7, exit_days_before_end=-1)


def test_holding_days():
    assert MonthEndFlow(entry_days_before_end=7, exit_days_before_end=1).holding_days == 6


def test_trades_one_per_month():
    df = _series(years=3)
    t = MonthEndFlow().trades(df)
    assert 30 <= len(t) <= 40
    assert all(isinstance(y, (int, np.integer)) for y, _ in t)


def test_exit_zero_holds_to_final_close():
    df = _series(bump_at_month_end=0.002, years=5)
    late = MonthEndFlow(entry_days_before_end=7, exit_days_before_end=0).trades(df)
    early = MonthEndFlow(entry_days_before_end=7, exit_days_before_end=3).trades(df)
    assert np.mean([r for _, r in late]) > np.mean([r for _, r in early])


# ------------------------------------------------------------------ guards
def test_real_effect_gives_a_plateau():
    df = _series(bump_at_month_end=0.002, noise=0.004, years=12)
    assert plateau_fraction(parameter_sweep(df)) >= 0.75


def test_no_effect_gives_no_plateau():
    df = _series(bump_at_month_end=0.0, drift=0.0, noise=0.006, seed=3, years=12)
    assert plateau_fraction(parameter_sweep(df)) < 0.75


def test_rest_of_month_control_detects_a_genuine_concentration():
    df = _series(bump_at_month_end=0.002, noise=0.003, years=12)
    c = rest_of_month_control(df, MonthEndFlow())
    assert c["t_stat"] > 2.0
    assert c["diff_pct"] > 0


def test_rest_of_month_control_rejects_pure_drift():
    """Steady drift with NO month-end concentration must read as noise —
    this is the check that unmasked SPY."""
    df = _series(bump_at_month_end=0.0, drift=0.0006, noise=0.003, years=12)
    c = rest_of_month_control(df, MonthEndFlow())
    assert abs(c["t_stat"]) < 2.0, "drift must not register as a month-end edge"


def test_duration_gradient_recovers_a_known_ordering():
    frames, durations = {}, {}
    for sym, dur, bump in [("A", 20.0, 0.0030), ("B", 10.0, 0.0015),
                           ("C", 5.0, 0.0008), ("D", 2.0, 0.0003)]:
        frames[sym] = _series(bump_at_month_end=bump, noise=0.002,
                              seed=hash(sym) % 100, years=12)
        durations[sym] = dur
    g = duration_gradient(frames, durations, MonthEndFlow())
    assert g["n"] == 4
    assert g["corr"] > 0.9


def test_walk_forward_flags_a_single_era_effect():
    df = _series(bump_at_month_end=0.003, noise=0.002,
                 bump_years=set(range(2015, 2019)), years=12)
    eras = walk_forward(MonthEndFlow().trades(df), ERAS)
    assert eras[0]["avg_pct"] > eras[-1]["avg_pct"]


def test_by_year_clusters_returns():
    df = _series(years=4)
    yb = by_year(MonthEndFlow().trades(df))
    assert all(k.isdigit() for k in yb)
    assert sum(len(v) for v in yb.values()) == len(MonthEndFlow().trades(df))


# ----------------------------------------------------------------- verdict
def test_validate_passes_a_genuine_flow():
    df = _series(bump_at_month_end=0.0025, noise=0.003, years=14)
    rep = validate(df, MonthEndFlow(), friction_pct=0.10, eras=ERAS)
    assert rep.passed is True
    assert rep.control["t_stat"] > 2
    assert "PASS" in rep.render()


def test_validate_fails_pure_noise():
    df = _series(bump_at_month_end=0.0, noise=0.006, seed=11, years=14)
    rep = validate(df, MonthEndFlow(), friction_pct=0.10, eras=ERAS)
    assert rep.passed is False


def test_validate_fails_when_effect_is_below_friction():
    """Measurable but too small to trade is still a FAIL."""
    df = _series(bump_at_month_end=0.0002, noise=0.001, years=14)
    rep = validate(df, MonthEndFlow(), friction_pct=0.50, eras=ERAS)
    assert rep.passed is False


def test_validate_fails_when_one_year_carries_it():
    df = _series(bump_at_month_end=0.05, noise=0.002,
                 bump_years={2020}, years=14)
    rep = validate(df, MonthEndFlow(), friction_pct=0.10, eras=ERAS)
    assert rep.without_best_year < rep.total_pct
    assert rep.passed is False


def test_validate_warns_when_control_also_shows_the_effect():
    df = _series(bump_at_month_end=0.0025, noise=0.003, years=14)
    rep = validate(df, MonthEndFlow(), friction_pct=0.10, eras=ERAS,
                   control_symbol_result={"t_stat": 4.0})
    assert any("not be instrument-specific" in n for n in rep.notes)


def test_validate_always_notes_correlated_instruments():
    df = _series(bump_at_month_end=0.0025, noise=0.003, years=14)
    rep = validate(df, MonthEndFlow(), friction_pct=0.10, eras=ERAS)
    assert any("independent observations" in n for n in rep.notes)
