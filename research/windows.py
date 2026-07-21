"""
research.windows
================
Point-in-time window construction.

The single rule this enforces: a signal ranks using data STRICTLY BEFORE a
cutoff and is evaluated only on bars at or after it. Violating it once produced
a fake +35.9% in this project that was really -64% — the ranking had peeked at
the evaluation window and "discovered" that stocks which rose, rose.

``truncate`` is the chokepoint. Use it for every feature computation rather
than slicing by hand, and the assertion cannot be forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import pandas as pd


@dataclass(frozen=True)
class Window:
    """One evaluation block. ``cutoff`` belongs to the evaluation side."""
    cutoff: pd.Timestamp
    end: pd.Timestamp

    @property
    def tag(self) -> str:
        return str(self.cutoff.date())

    def __str__(self) -> str:
        return f"{self.cutoff.date()}..{self.end.date()}"


def point_in_time_windows(trading_days: Sequence[pd.Timestamp], window_days: int,
                          n_windows: int, disjoint: bool = True) -> List[Window]:
    """
    Build evaluation windows over a trading calendar.

    Overlapping windows inflate apparent sample size — the same bars get
    counted more than once — so ``disjoint`` defaults to True. Only relax it
    if the analysis explicitly accounts for the correlation.
    """
    days = list(trading_days)
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    if len(days) < window_days + 1:
        raise ValueError(f"need > {window_days} trading days, got {len(days)}")

    if disjoint:
        max_windows = len(days) // window_days
        n = min(n_windows, max_windows)
        if n < 1:
            raise ValueError("calendar too short for even one window")
        starts = [len(days) - (i + 1) * window_days for i in range(n)][::-1]
    else:
        import numpy as np
        starts = sorted(set(np.linspace(
            0, len(days) - window_days - 1, n_windows).astype(int).tolist()))

    return [Window(cutoff=days[s], end=days[min(s + window_days, len(days) - 1)])
            for s in starts if s >= 0]


def truncate(df: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """
    Bars strictly before ``cutoff`` — the only history a signal may rank on.

    Asserts rather than trusting the caller: a silent lookahead is the most
    expensive bug in this codebase's history.
    """
    if df is None or df.empty:
        return df
    out = df[df.index < cutoff]
    if not out.empty:
        assert out.index[-1] < cutoff, (
            f"lookahead: last selection bar {out.index[-1]} >= cutoff {cutoff}")
    return out


def assert_evaluation_bars(df: pd.DataFrame, cutoff: pd.Timestamp) -> None:
    """Assert evaluation bars do not precede the cutoff (the mirror check)."""
    if df is None or df.empty:
        return
    first = df.index[0]
    first = first.tz_localize(None) if first.tzinfo else first
    cut = cutoff.tz_localize(None) if cutoff.tzinfo else cutoff
    assert first.normalize() >= cut.normalize(), (
        f"evaluation bars start {first} before cutoff {cutoff}")
