"""
Research guards — each test replays a false positive this project produced,
so a future change that weakens a guard fails here rather than in production.
"""

import numpy as np
import pandas as pd
import pytest

from research import evaluate, point_in_time_windows
from research.guards import (
    cluster_bootstrap_ci, drop_best_contributor, matched_random_control,
    monotonicity, multiple_comparisons_note,
)
from research.windows import Window, assert_evaluation_bars, truncate


# --------------------------------------------------------------- guards
def test_drop_best_contributor_unmasks_a_one_symbol_result():
    """The scored_top5 case: +0.22% total that was one symbol's luck."""
    clusters = {"SAREGAMA": [9.0], "A": [-2.0], "B": [-3.0], "C": [-3.5]}
    assert sum(sum(v) for v in clusters.values()) == pytest.approx(0.5)
    assert drop_best_contributor(clusters) == pytest.approx(-8.5)


def test_cluster_bootstrap_is_wider_than_iid():
    """
    Clustered trades carry less information than their count suggests.

    Each cluster gets a shared offset — the real situation, where every trade
    in one symbol on one day rides the same price path. That within-cluster
    correlation is exactly what an i.i.d. bootstrap ignores.
    """
    rng = np.random.default_rng(0)
    clusters = {}
    for i in range(8):
        block_effect = rng.normal(0.0, 1.5)          # the shared path
        clusters[f"s{i}"] = list(block_effect + rng.normal(0.5, 0.2, 20))
    lo_c, hi_c = cluster_bootstrap_ci(clusters, n_boot=2000)
    flat = [r for v in clusters.values() for r in v]
    boot = np.array([rng.choice(flat, len(flat), replace=True).sum()
                     for _ in range(2000)])
    lo_i, hi_i = np.percentile(boot, [2.5, 97.5])
    assert (hi_c - lo_c) > (hi_i - lo_i)


def test_monotonicity_rejects_the_entry_gate_shape():
    """top-10% worse than top-20% is noise, not concentrating signal."""
    assert monotonicity({1.0: -0.294, 0.5: -0.265, 0.3: -0.127,
                         0.2: 0.029, 0.1: -0.067}) is False


def test_monotonicity_accepts_a_concentrating_signal():
    assert monotonicity({1.0: 0.1, 0.5: 0.2, 0.3: 0.35, 0.1: 0.6}) is True


def test_monotonicity_needs_three_thresholds():
    assert monotonicity({1.0: 0.1, 0.5: 0.2}) is None


def test_matched_random_control_ignores_mere_shrinkage():
    """Taking fewer trades cuts losses mechanically; that must not read as skill."""
    rng = np.random.default_rng(1)
    universe = list(rng.normal(-0.3, 1.0, 1000))
    k = 100
    # The EXPECTED total of a random basket, not one lucky draw of it.
    typical = float(np.mean(universe)) * k
    p = matched_random_control(universe, k, typical, n_boot=2000)
    assert 0.3 < p < 0.7, "an average basket must not look significant"


def test_matched_random_control_detects_real_selection():
    rng = np.random.default_rng(2)
    universe = list(rng.normal(-0.3, 1.0, 1000))
    best = float(np.sum(sorted(universe)[-100:]))
    assert matched_random_control(universe, 100, best, n_boot=2000) < 0.01


def test_multiple_comparisons_note_scales_with_variants():
    assert multiple_comparisons_note(1) is None
    assert "0.23" in multiple_comparisons_note(5)


# --------------------------------------------------------------- verdict
def test_evaluate_fails_a_one_symbol_carried_result():
    rep = evaluate(
        name="carried_by_one",
        returns_by_cluster={"WIN": [9.0], "a": [-2.0], "b": [-3.0], "c": [-3.5]},
        universe_returns=list(np.random.default_rng(0).normal(-0.3, 1.0, 500)),
        friction=0.22,
    )
    assert rep.passed is False
    assert rep.without_best_cluster < 0


def test_evaluate_flags_effect_smaller_than_friction():
    """Regime timing died here: measurable, but under the hurdle."""
    rng = np.random.default_rng(3)
    clusters = {f"s{i}": list(rng.normal(0.11, 0.05, 10)) for i in range(30)}
    rep = evaluate("tiny_effect", clusters,
                   list(rng.normal(0.0, 1.0, 500)), friction=0.22)
    assert any("smaller than friction" in n for n in rep.notes)


def test_evaluate_can_pass_a_genuine_signal():
    """The guards must not be unfalsifiable — real edge has to survive."""
    rng = np.random.default_rng(4)
    clusters = {f"s{i}": list(rng.normal(1.5, 0.4, 10)) for i in range(40)}
    rep = evaluate("real_edge", clusters, list(rng.normal(-0.3, 1.0, 2000)),
                   friction=0.22,
                   totals_by_threshold={1.0: 0.5, 0.5: 0.9, 0.2: 1.4})
    assert rep.passed is True
    assert rep.ci_low > 0


def test_report_renders_verdict():
    rep = evaluate("x", {"a": [1.0]}, [0.0, 1.0], friction=0.22)
    assert rep.render().startswith(("[PASS]", "[FAIL]"))


# --------------------------------------------------------------- windows
def _calendar(n=100):
    return list(pd.date_range("2026-01-01", periods=n, freq="B"))


def test_windows_are_disjoint_by_default():
    ws = point_in_time_windows(_calendar(), window_days=10, n_windows=5)
    assert len(ws) == 5
    for a, b in zip(ws, ws[1:]):
        assert a.end <= b.cutoff, "windows must not share bars"


def test_windows_capped_by_calendar_length():
    ws = point_in_time_windows(_calendar(25), window_days=10, n_windows=99)
    assert len(ws) == 2


def test_window_too_short_raises():
    with pytest.raises(ValueError):
        point_in_time_windows(_calendar(5), window_days=10, n_windows=1)


def test_truncate_excludes_the_cutoff_bar():
    cal = _calendar(30)
    df = pd.DataFrame({"Close": range(30)}, index=cal)
    cut = cal[20]
    out = truncate(df, cut)
    assert out.index[-1] < cut
    assert len(out) == 20


def test_truncate_is_the_lookahead_chokepoint():
    """The +35.9% fake came from ranking on evaluation-window bars."""
    cal = _calendar(30)
    df = pd.DataFrame({"Close": range(30)}, index=cal)
    for cut in (cal[5], cal[15], cal[29]):
        assert (truncate(df, cut).index >= cut).sum() == 0


def test_assert_evaluation_bars_rejects_early_bars():
    cal = _calendar(30)
    df = pd.DataFrame({"Close": range(30)}, index=cal)
    assert_evaluation_bars(df.loc[cal[10]:], cal[10])
    with pytest.raises(AssertionError):
        assert_evaluation_bars(df, cal[10])


def test_window_tag_is_the_cutoff_date():
    w = Window(cutoff=pd.Timestamp("2026-05-26"), end=pd.Timestamp("2026-06-09"))
    assert w.tag == "2026-05-26"
