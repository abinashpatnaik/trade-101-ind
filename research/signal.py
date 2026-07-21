"""
research.signal
===============
The interface a candidate signal implements to be testable.

A hypothesis supplies ``rank`` — given each symbol's history truncated to
before the cutoff, return a score per symbol. The harness handles windows,
replay, friction and guards, so a new idea is a ~20-line class rather than a
bespoke script that quietly reinvents (and mis-implements) the protocol.

Deliberately narrow: ``rank`` receives ALREADY-TRUNCATED history. A signal
cannot look ahead even by accident, because it is never handed future bars.

Example
-------
    class PullbackInUptrend(Signal):
        name = "pullback_in_uptrend"

        def rank(self, histories, cutoff):
            out = {}
            for sym, df in histories.items():
                c = df["Close"]
                if len(c) < 60:
                    continue
                sma50 = c.rolling(50).mean().iloc[-1]
                if c.iloc[-1] <= sma50:
                    continue                      # not an uptrend
                drawdown = c.iloc[-1] / c.iloc[-20:].max() - 1
                if not (-0.15 <= drawdown <= -0.03):
                    continue                      # not resting
                out[sym] = float(c.iloc[-1] / c.iloc[-61] - 1)
            return out
"""

from __future__ import annotations

from typing import Dict, Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class Signal(Protocol):
    """A candidate stock-selection hypothesis."""

    name: str

    def rank(self, histories: Dict[str, pd.DataFrame],
             cutoff: pd.Timestamp) -> Dict[str, float]:
        """
        Score symbols using only pre-cutoff history.

        Parameters
        ----------
        histories:
            ``{symbol: DataFrame}`` already truncated to bars strictly before
            ``cutoff`` by ``research.windows.truncate``. Never contains future
            bars, so lookahead is structurally impossible here.
        cutoff:
            Start of the evaluation window. Provided for calendar logic only.

        Returns
        -------
        ``{symbol: score}`` — higher is better. Omit a symbol to exclude it;
        an empty dict means "no candidates this window", which is a legitimate
        answer and is recorded as zero trades rather than an error.
        """
        ...


class TopNSelector:
    """Wraps a Signal into 'take the N best-scoring symbols'."""

    def __init__(self, signal: Signal, n: int = 15) -> None:
        self.signal = signal
        self.n = n
        self.name = f"{signal.name}_top{n}"

    def select(self, histories: Dict[str, pd.DataFrame],
               cutoff: pd.Timestamp) -> list:
        scores = self.signal.rank(histories, cutoff)
        return [s for s, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:self.n]]
