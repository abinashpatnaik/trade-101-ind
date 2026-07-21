"""
research.edge_study
===================
"Does the strategy have gross edge in ANY market regime?"

Distinct from ``harness.run_study``, which asks whether a SELECTION rule beats
random picking. This holds the universe fixed and asks the prior question:
does the entry/exit system itself make money before friction, and does that
change across regimes?

GROSS is the number that matters. Friction is a known constant; edge is not.
A system with zero gross edge cannot be rescued by cheaper execution, better
selection, or a smarter decision layer — there is nothing to allocate.

Ran for US on 2026-07-21 over 24 windows / 4,407 trades / 5 years:
    gross +0.0064%/trade, 95% CI [-0.112%, +0.143%], friction 0.220%
    => even the CI's upper bound sits below friction. No edge, any regime.
    Per-window sd was 0.322 (> friction), but regime features could not
    predict which windows would be good — see the regime notes in memory.

Windows are spread evenly across the available history rather than clustered
at the recent end, so a single regime cannot dominate the verdict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from research.data import BarSource
from research.windows import Window, assert_evaluation_bars, point_in_time_windows

logger = logging.getLogger(__name__)


@dataclass
class WindowEdge:
    window: str
    n_trades: int
    win_rate: float
    gross_avg: float
    gross_total: float
    net_total: float


@dataclass
class EdgeStudyResult:
    market: str
    friction_pct: float
    windows: List[WindowEdge] = field(default_factory=list)
    trades_by_cluster: Dict[str, List[float]] = field(default_factory=dict)
    exit_reasons: Dict[str, List[float]] = field(default_factory=dict)

    @property
    def all_net(self) -> np.ndarray:
        return np.array([r for v in self.trades_by_cluster.values() for r in v])

    def summary(self) -> dict:
        net = self.all_net
        if not len(net):
            return {"trades": 0}
        gross = net + self.friction_pct
        per_window = np.array([w.gross_avg for w in self.windows])
        rng = np.random.default_rng(0)
        boot = np.array([rng.choice(per_window, len(per_window), replace=True).mean()
                         for _ in range(10000)]) if len(per_window) > 1 else np.array([0.0])
        return {
            "trades": int(len(net)),
            "windows": len(self.windows),
            "win_rate": float(100 * (net > 0).mean()),
            "gross_avg": float(gross.mean()),
            "gross_total": float(gross.sum()),
            "net_total": float(net.sum()),
            "friction": self.friction_pct,
            "windows_positive": int((per_window > 0).sum()),
            "window_sd": float(per_window.std()) if len(per_window) > 1 else 0.0,
            "ci_low": float(np.percentile(boot, 2.5)),
            "ci_high": float(np.percentile(boot, 97.5)),
        }

    def render(self) -> str:
        s = self.summary()
        if not s.get("trades"):
            return "no trades produced"
        lines = [
            "=" * 84,
            f"GROSS EDGE BY WINDOW — market={self.market}",
            "=" * 84,
            f"{'window':<14}{'trades':>8}{'win%':>8}{'gross avg%':>12}"
            f"{'gross tot%':>12}{'net tot%':>11}",
            "-" * 84,
        ]
        for w in self.windows:
            lines.append(f"{w.window:<14}{w.n_trades:>8}{w.win_rate:>7.1f}%"
                         f"{w.gross_avg:>12.4f}{w.gross_total:>12.2f}{w.net_total:>11.2f}")
        lines += [
            "-" * 84,
            f"{'POOLED':<14}{s['trades']:>8}{s['win_rate']:>7.1f}%"
            f"{s['gross_avg']:>12.4f}{s['gross_total']:>12.2f}{s['net_total']:>11.2f}",
            "",
            f"  windows with positive gross edge: {s['windows_positive']}/{s['windows']}",
            f"  95% CI on mean gross edge/trade: "
            f"[{s['ci_low']:+.4f}%, {s['ci_high']:+.4f}%]",
            f"  friction to beat: {s['friction']:.3f}%",
            f"  per-window sd: {s['window_sd']:.4f}",
        ]
        if s["ci_high"] < s["friction"]:
            lines.append("  VERDICT: even the CI's upper bound is BELOW friction — "
                         "no edge to allocate, in any regime sampled.")
        elif s["ci_low"] > s["friction"]:
            lines.append("  VERDICT: gross edge exceeds friction — investigate further.")
        else:
            lines.append("  VERDICT: inconclusive — CI spans the friction hurdle.")
        if s["window_sd"] > s["friction"]:
            lines.append(f"  NOTE: per-window sd ({s['window_sd']:.3f}) exceeds friction "
                         f"({s['friction']:.3f}). If — and only if — that variation were "
                         "predictable in advance, regime timing could clear costs. "
                         "Test predictability before believing it.")
        if self.exit_reasons:
            lines += ["", "  exit mix:"]
            for reason, rets in sorted(self.exit_reasons.items(),
                                       key=lambda kv: np.sum(kv[1])):
                a = np.array(rets)
                lines.append(f"    {reason:<22}{len(a):>6}  avg {a.mean():>7.3f}%"
                             f"  total {a.sum():>10.2f}%")
        return "\n".join(lines)


def run_edge_study(
    symbols: Sequence[str],
    market: str = "IN",
    n_windows: int = 24,
    window_days: int = 10,
    years: int = 5,
    calendar_reference: Optional[str] = None,
    source: Optional[BarSource] = None,
    replay_fn: Optional[Callable] = None,
    cache_dir: Optional[str] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> EdgeStudyResult:
    """Replay a FIXED universe across evenly-spread windows and measure gross edge."""
    market = market.upper()
    src = source or (BarSource(market, cache_dir) if cache_dir else BarSource(market))

    if replay_fn is None:
        from research.harness import _default_replay_fn
        replay_fn = _default_replay_fn()

    from agents.backtest_sim import SimParams
    friction = SimParams().round_trip_cost_pct * 100.0

    ref = calendar_reference or ("SPY" if market == "US" else list(symbols)[0])
    calendar = src.trading_calendar(ref, years=years)
    limit = src.intraday_limit_days()
    if limit is not None:
        oldest = pd.Timestamp.now("UTC").normalize().tz_localize(None) - pd.Timedelta(days=limit)
        calendar = [d for d in calendar
                    if (d.tz_localize(None) if d.tzinfo else d) >= oldest]

    # Evenly spread (not disjoint-from-the-end) so all regimes are sampled.
    windows: List[Window] = point_in_time_windows(
        calendar, window_days, n_windows, disjoint=False)
    logger.info("%s: %d windows, %s .. %s", market, len(windows),
                windows[0].cutoff.date(), windows[-1].end.date())

    result = EdgeStudyResult(market=market, friction_pct=friction)
    total = len(windows) * len(symbols)
    done = 0

    for w in windows:
        nets: List[float] = []
        for sym in symbols:
            bars = src.bars_5m(sym, w.cutoff.to_pydatetime(), w.end.to_pydatetime())
            done += 1
            if progress:
                progress(done, total)
            if bars is None or bars.empty:
                continue
            try:
                assert_evaluation_bars(bars, w.cutoff)
            except AssertionError as exc:
                logger.warning("skip %s %s: %s", sym, w.tag, exc)
                continue
            out = replay_fn(sym, bars)
            rets = [r for r, _ in out] if out and isinstance(out[0], tuple) else (out or [])
            reasons = [x for _, x in out] if out and isinstance(out[0], tuple) else []
            if rets:
                result.trades_by_cluster[f"{sym}|{w.tag}"] = list(rets)
                nets.extend(rets)
                for r, reason in zip(rets, reasons):
                    result.exit_reasons.setdefault(reason, []).append(r)
        if nets:
            arr = np.array(nets)
            result.windows.append(WindowEdge(
                window=w.tag, n_trades=len(arr),
                win_rate=float(100 * (arr > 0).mean()),
                gross_avg=float((arr + friction).mean()),
                gross_total=float((arr + friction).sum()),
                net_total=float(arr.sum())))
    return result
