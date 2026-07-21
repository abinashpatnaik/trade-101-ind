"""
research.harness
================
Runs a candidate Signal end-to-end and returns a guarded verdict.

Pipeline per window:
  1. daily histories truncated to bars strictly BEFORE the cutoff
     (via ``windows.truncate`` — a signal never receives future bars);
  2. the Signal ranks and the top N are selected;
  3. an equal-sized RANDOM basket is drawn from the same eligible pool —
     this is the control, and it is the whole point. Selecting fewer trades
     mechanically reduces friction losses, so a signal must beat random
     selection of the same size, not merely beat trading everything;
  4. both baskets are replayed through the LIVE entry path
     (``agents.backtest_sim.replay`` with the real ML validator), so the
     study tests the system that actually trades;
  5. trades are clustered by ``symbol|window`` for the guards, because
     trades sharing a symbol and day share a price path and are not
     independent observations.

Cost note: the control doubles replay cost. That is deliberate — three
false positives this session came from a missing or naive control.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List, Optional, Sequence

import pandas as pd

from research.data import BarSource
from research.guards import GuardReport, evaluate
from research.signal import Signal
from research.windows import Window, assert_evaluation_bars, point_in_time_windows, truncate

logger = logging.getLogger(__name__)


@dataclass
class StudyResult:
    signal_name: str
    market: str
    windows: List[Window]
    friction_pct: float
    selected: Dict[str, List[float]] = field(default_factory=dict)   # cluster -> returns
    control: Dict[str, List[float]] = field(default_factory=dict)
    picks: Dict[str, List[str]] = field(default_factory=dict)        # window tag -> symbols
    warnings: List[str] = field(default_factory=list)

    @property
    def n_trades(self) -> int:
        return sum(len(v) for v in self.selected.values())

    def report(self, n_variants_tried: int = 1) -> GuardReport:
        flat_control = [r for v in self.control.values() for r in v]
        rep = evaluate(
            name=f"{self.signal_name} [{self.market}]",
            returns_by_cluster=self.selected,
            universe_returns=flat_control,
            friction=self.friction_pct,
            n_variants_tried=n_variants_tried,
            universe_clusters=self.control or None,
        )
        rep.notes.extend(self.warnings)
        return rep


def _liquid(df: pd.DataFrame, min_price: float, min_volume: float) -> bool:
    if df is None or len(df) < 60:
        return False
    try:
        return (float(df["Close"].iloc[-1]) >= min_price
                and float(df["Volume"].iloc[-20:].mean()) >= min_volume)
    except Exception:
        return False


def run_study(
    signal: Signal,
    symbols: Sequence[str],
    market: str = "IN",
    n_windows: int = 8,
    window_days: int = 10,
    top_n: int = 15,
    calendar_reference: Optional[str] = None,
    min_price: float = 50.0,
    min_volume: float = 1_000_000.0,
    cache_dir: Optional[str] = None,
    seed: int = 0,
    source: Optional[BarSource] = None,
    replay_fn=None,
) -> StudyResult:
    """
    Execute a point-in-time study. ``replay_fn`` is injectable for testing;
    it defaults to the live replay engine.
    """
    market = market.upper()
    src = source or (BarSource(market, cache_dir) if cache_dir else BarSource(market))
    rng = random.Random(seed)

    if replay_fn is None:
        replay_fn = _default_replay_fn()

    from agents.backtest_sim import SimParams
    friction = SimParams().round_trip_cost_pct * 100.0

    ref = calendar_reference or ("SPY" if market == "US" else list(symbols)[0])
    calendar = src.trading_calendar(ref, years=5 if market == "US" else 2)

    # Intraday history is the binding constraint on how far back windows can go.
    limit = src.intraday_limit_days()
    warnings: List[str] = []
    if limit is not None:
        oldest = pd.Timestamp.now("UTC").normalize().tz_localize(None) - timedelta(days=limit)
        calendar = [d for d in calendar
                    if (d.tz_localize(None) if d.tzinfo else d) >= oldest]
        warnings.append(
            f"{market} intraday history is capped at ~{limit} days, so windows "
            f"sit inside one market regime — treat this as directional only.")

    windows = point_in_time_windows(calendar, window_days, n_windows, disjoint=True)
    if len(windows) < n_windows:
        warnings.append(f"only {len(windows)} disjoint windows fit the available history.")

    daily = src.daily(list(symbols), years=5 if market == "US" else 2)
    result = StudyResult(signal_name=getattr(signal, "name", type(signal).__name__),
                         market=market, windows=windows, friction_pct=friction,
                         warnings=warnings)

    for w in windows:
        histories = {}
        for sym, df in daily.items():
            hist = truncate(df, w.cutoff)          # the lookahead chokepoint
            if _liquid(hist, min_price, min_volume):
                histories[sym] = hist
        if not histories:
            continue

        picks = [s for s, _ in sorted(signal.rank(histories, w.cutoff).items(),
                                      key=lambda kv: -kv[1])[:top_n]]
        result.picks[w.tag] = picks

        # Matched-size random control from the SAME eligible pool.
        pool = [s for s in histories if s not in picks]
        control = rng.sample(pool, min(len(picks), len(pool))) if pool else []

        for group, syms in (("selected", picks), ("control", control)):
            target = result.selected if group == "selected" else result.control
            for sym in syms:
                bars = src.bars_5m(sym, w.cutoff.to_pydatetime(), w.end.to_pydatetime())
                if bars is None or bars.empty:
                    continue
                try:
                    assert_evaluation_bars(bars, w.cutoff)
                except AssertionError as exc:
                    logger.warning("skipping %s in %s: %s", sym, w.tag, exc)
                    continue
                rets = replay_fn(sym, bars)
                if rets:
                    target[f"{sym}|{w.tag}"] = rets

    return result


def _default_replay_fn():
    """Live entry path: real ML validator, real decision engine, real exit math."""
    from agents.backtest_sim import SimParams, replay
    from ai_validator import AIValidator
    from decision_engine import DecisionEngine
    from trend_engine import TrendEngine

    te, de, ai, params = TrendEngine(), DecisionEngine(), AIValidator(), SimParams()
    if ai.model_day is None:
        raise RuntimeError(
            "ML day model not loaded — the replay would silently fall back to "
            "the classic trend path and test the wrong system.")

    def _run(symbol: str, bars: pd.DataFrame) -> List[float]:
        if len(bars) <= params.warmup_bars:
            return []
        try:
            r = replay(symbol, bars, de, te, params, ai)
        except Exception as exc:
            logger.warning("replay failed for %s: %s", symbol, exc)
            return []
        return [] if r.error else [t.return_pct for t in r.trades]

    return _run
