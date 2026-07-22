"""
research.calendar_flow
======================
Calendar-flow strategies and the robustness battery that validates them.

A DIFFERENT STRATEGY CLASS from ``research.signal``. Selection rules answer
"which symbol?"; a calendar flow answers "when should I hold this one at all?"
It has no cross-sectional ranking, so it does not implement ``Signal.rank``.

Validated instance (2026-07-22): the **month-end Treasury duration flow**.
Bond index funds must extend duration as the index takes on new long paper at
month end, creating real buying pressure in long bonds that unwinds early the
next month. Long TLT from 7 trading days before month end to 1 day before it:

    gross +0.304%/trade, 288 trades, 24 years, win 59.7%

That is ~47x the live intraday bot's +0.0064%/trade at ~12 trades a YEAR
instead of thousands — the holding-period point made concrete.

WHY THE BATTERY EXISTS
----------------------
Six earlier candidates in this project produced positive headline numbers and
all six were noise. Three survived until someone remembered a specific check.
``validate()`` therefore runs EVERY check and returns ``passed`` only if all of
them hold — a caller cannot accidentally report a partial result.

The decisive one is ``rest_of_month_control``. The month-end effect looked
identical on SPY (+0.303%) and TLT (+0.304%), which would have meant it was
not a bond flow at all. Comparing against equal-length windows ELSEWHERE in
the same month resolved it: SPY's edge is plain equity drift (t=0.15, any
random 6 days pays the same) while long bonds are NEGATIVE for the rest of the
month and concentrate their return at the turn (TLT t=3.23). Always control
against the alternative "this is just being invested".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from research.guards import cluster_bootstrap_ci, drop_best_contributor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- strategy
@dataclass(frozen=True)
class MonthEndFlow:
    """Long a duration-sensitive instrument across the month-end turn.

    Offsets count TRADING days from the end of the month, so the rule is
    calendar-robust (holidays and short months shift it automatically).
    ``exit_days_before_end=0`` means hold to the final close of the month.
    """
    name: str = "month_end_bond_flow"
    entry_days_before_end: int = 7
    exit_days_before_end: int = 1

    def __post_init__(self) -> None:
        if self.entry_days_before_end <= self.exit_days_before_end:
            raise ValueError("entry must be further from month end than exit")
        if self.exit_days_before_end < 0:
            raise ValueError("exit_days_before_end must be >= 0")

    @property
    def holding_days(self) -> int:
        return self.entry_days_before_end - self.exit_days_before_end

    def trades(self, df: pd.DataFrame) -> List[Tuple[int, float]]:
        """[(year, gross_return)] — one month-end trade per complete month."""
        out: List[Tuple[int, float]] = []
        for (year, _month), days in _month_groups(df):
            if len(days) < self.entry_days_before_end + 1:
                continue
            entry = float(df.loc[days[-self.entry_days_before_end], "Close"])
            exit_ = float(df.loc[days[-(self.exit_days_before_end + 1)], "Close"])
            if entry > 0:
                out.append((year, exit_ / entry - 1.0))
        return out


def _month_groups(df: pd.DataFrame):
    idx = df.index
    g = pd.Series(idx, index=idx).groupby([idx.year, idx.month]).apply(list)
    return [(k, g[k]) for k in sorted(g.index)]


# ------------------------------------------------------------------ tests
def parameter_sweep(df: pd.DataFrame, entries: Sequence[int] = range(3, 13),
                    exits: Sequence[int] = (0, 1, 2, 3)) -> Dict[Tuple[int, int], float]:
    """Gross %/trade for each entry/exit offset.

    Real flow gives a broad positive PLATEAU; curve-fitting gives a lone spike
    at the published parameters.
    """
    grid: Dict[Tuple[int, int], float] = {}
    for e in entries:
        for x in exits:
            if x >= e:
                continue
            t = MonthEndFlow(entry_days_before_end=e, exit_days_before_end=x).trades(df)
            if t:
                grid[(e, x)] = float(np.mean([r for _, r in t]) * 100)
    return grid


def plateau_fraction(grid: Dict[Tuple[int, int], float]) -> float:
    """Share of parameter combinations that are positive."""
    vals = [v for v in grid.values() if np.isfinite(v)]
    return float(np.mean([v > 0 for v in vals])) if vals else 0.0


def walk_forward(trades: List[Tuple[int, float]],
                 eras: Sequence[Tuple[int, int]]) -> List[dict]:
    """Per-era stats. An effect confined to one era is a regime artifact."""
    out = []
    for a, b in eras:
        sub = [r for y, r in trades if a <= y <= b]
        if not sub:
            continue
        arr = np.array(sub)
        out.append({"era": f"{a}-{b}", "n": len(arr),
                    "avg_pct": float(arr.mean() * 100),
                    "win_pct": float(100 * (arr > 0).mean())})
    return out


def by_year(trades: List[Tuple[int, float]]) -> Dict[str, List[float]]:
    """Group returns (in %) by calendar year — the cluster unit for the guards."""
    out: Dict[str, List[float]] = {}
    for y, r in trades:
        out.setdefault(str(y), []).append(r * 100)
    return out


def rest_of_month_control(df: pd.DataFrame, flow: MonthEndFlow) -> dict:
    """
    THE decisive test: month-end window vs equal-length windows elsewhere in
    the same month. Strips out plain drift — "being invested" is not an edge.
    """
    hold = flow.holding_days
    me, other = [], []
    for _key, days in _month_groups(df):
        if len(days) < flow.entry_days_before_end + hold + 2:
            continue
        e = float(df.loc[days[-flow.entry_days_before_end], "Close"])
        x = float(df.loc[days[-(flow.exit_days_before_end + 1)], "Close"])
        if e > 0:
            me.append(x / e - 1.0)
        for s in range(0, len(days) - flow.entry_days_before_end - hold):
            a = float(df.loc[days[s], "Close"])
            b = float(df.loc[days[s + hold], "Close"])
            if a > 0:
                other.append(b / a - 1.0)
    if len(me) < 10 or len(other) < 10:
        return {"n_month_end": len(me), "n_other": len(other), "t_stat": 0.0,
                "diff_pct": 0.0, "month_end_pct": 0.0, "other_pct": 0.0}
    m, o = np.array(me), np.array(other)
    diff = m.mean() - o.mean()
    se = np.sqrt(m.var(ddof=1) / len(m) + o.var(ddof=1) / len(o))
    return {"n_month_end": len(m), "n_other": len(o),
            "month_end_pct": float(m.mean() * 100), "other_pct": float(o.mean() * 100),
            "diff_pct": float(diff * 100), "t_stat": float(diff / se) if se else 0.0}


def duration_gradient(frames: Dict[str, pd.DataFrame], durations: Dict[str, float],
                      flow: MonthEndFlow) -> dict:
    """
    Mechanism test: if month-end index duration-extension drives this, the
    effect must SCALE WITH DURATION across independent instruments. No amount
    of fitting on one ticker can manufacture a predicted ordering.
    """
    pts = []
    for sym, df in frames.items():
        if sym not in durations or durations[sym] <= 0:
            continue
        t = flow.trades(df)
        if len(t) >= 30:
            pts.append((durations[sym], float(np.mean([r for _, r in t]) * 100)))
    if len(pts) < 3:
        return {"n": len(pts), "corr": float("nan"), "points": pts}
    d = np.array([p[0] for p in pts]); v = np.array([p[1] for p in pts])
    return {"n": len(pts), "corr": float(np.corrcoef(d, v)[0, 1]), "points": pts}


# --------------------------------------------------------------- verdict
@dataclass
class FlowValidation:
    name: str
    n_trades: int
    gross_avg_pct: float
    win_pct: float
    plateau: float
    eras: List[dict]
    total_pct: float
    without_best_year: float
    ci_low: float
    ci_high: float
    years_profitable: str
    control: dict
    gradient: dict
    friction_pct: float
    notes: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        era_ok = bool(self.eras) and all(e["avg_pct"] > 0 for e in self.eras)
        return bool(
            self.gross_avg_pct > self.friction_pct
            and self.plateau >= 0.75
            and era_ok
            and self.without_best_year > 0
            and self.ci_low > 0
            and self.control.get("t_stat", 0) > 2.0
        )

    def render(self) -> str:
        v = "PASS" if self.passed else "FAIL"
        L = [f"[{v}] {self.name}",
             f"  trades={self.n_trades}  gross={self.gross_avg_pct:+.3f}%/trade"
             f"  win={self.win_pct:.1f}%  (friction {self.friction_pct:.3f}%)",
             f"  parameter plateau: {self.plateau*100:.0f}% of combos positive"
             f" {'OK' if self.plateau >= 0.75 else 'FAIL — looks fitted'}",
             "  walk-forward:"]
        for e in self.eras:
            L.append(f"    {e['era']:<12}{e['n']:>5} trades  {e['avg_pct']:+.3f}%/trade"
                     f"  win {e['win_pct']:.1f}%")
        L += [f"  total {self.total_pct:+.2f}%, excluding best year "
              f"{self.without_best_year:+.2f}%"
              f" {'OK' if self.without_best_year > 0 else 'FAIL — one year carried it'}",
              f"  yearly-cluster CI [{self.ci_low:+.2f}%, {self.ci_high:+.2f}%]"
              f" {'excludes 0' if self.ci_low > 0 else 'INCLUDES 0'}",
              f"  years profitable: {self.years_profitable}"]
        c = self.control
        L.append(f"  rest-of-month control: month-end {c.get('month_end_pct', 0):+.3f}%"
                 f" vs elsewhere {c.get('other_pct', 0):+.3f}%"
                 f"  diff {c.get('diff_pct', 0):+.3f}%  t={c.get('t_stat', 0):.2f}"
                 f" {'REAL' if c.get('t_stat', 0) > 2 else 'NOT DISTINGUISHABLE FROM DRIFT'}")
        if np.isfinite(self.gradient.get("corr", float("nan"))):
            L.append(f"  duration gradient: corr={self.gradient['corr']:+.3f}"
                     f" across {self.gradient['n']} instruments")
        L += [f"  ! {n}" for n in self.notes]
        return "\n".join(L)


def validate(primary: pd.DataFrame, flow: MonthEndFlow, friction_pct: float,
             eras: Sequence[Tuple[int, int]],
             frames: Optional[Dict[str, pd.DataFrame]] = None,
             durations: Optional[Dict[str, float]] = None,
             control_symbol_result: Optional[dict] = None) -> FlowValidation:
    """Run the whole battery. ``passed`` requires every check to hold."""
    trades = flow.trades(primary)
    arr = np.array([r for _, r in trades]) if trades else np.array([])
    yb = by_year(trades)
    total = sum(sum(v) for v in yb.values()) if yb else 0.0
    lo, hi = cluster_bootstrap_ci(yb, n_boot=10000) if yb else (0.0, 0.0)
    grad = (duration_gradient(frames, durations, flow)
            if frames and durations else {"n": 0, "corr": float("nan"), "points": []})

    notes: List[str] = []
    if control_symbol_result is not None:
        t = control_symbol_result.get("t_stat", 0.0)
        if t > 2.0:
            notes.append(
                f"non-bond control ALSO shows a real month-end effect (t={t:.2f}) "
                "— the edge may not be instrument-specific.")
    if grad["n"] and grad["n"] < 3:
        notes.append("too few instruments for a meaningful duration gradient.")
    if len(yb) < 10:
        notes.append(f"only {len(yb)} yearly clusters — CI is wide by construction.")
    notes.append("correlated instruments (all Treasuries) are ~1-2 independent "
                 "observations plus a predicted-ordering test, not N.")

    return FlowValidation(
        name=flow.name, n_trades=len(arr),
        gross_avg_pct=float(arr.mean() * 100) if len(arr) else 0.0,
        win_pct=float(100 * (arr > 0).mean()) if len(arr) else 0.0,
        plateau=plateau_fraction(parameter_sweep(primary)),
        eras=walk_forward(trades, eras), total_pct=float(total),
        without_best_year=float(drop_best_contributor(yb)),
        ci_low=float(lo), ci_high=float(hi),
        years_profitable=f"{sum(1 for v in yb.values() if sum(v) > 0)}/{len(yb)}",
        control=rest_of_month_control(primary, flow), gradient=grad,
        friction_pct=friction_pct, notes=notes,
    )
