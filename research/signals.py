"""
research.signals
================
Catalogue of candidate selection signals.

The first four are the rules already tested on India this session. They are
kept as REFERENCE POINTS, not recommendations — every one measured at zero
gross edge. A new hypothesis should be compared against ``Momentum20`` (the
rule the live screener uses) and against ``run_study``'s random control.

Measured, point-in-time, net of friction (IN, Nifty 500, 4 x 10-day windows):

    momentum20        -214.5%   (the live rule)
    pullback_uptrend  -154.4%
    lowrsi_uptrend    -173.0%
    anti_momentum      -95.3%   (control: the mirror of the live rule)
    liquidity_only    -139.2%   (control: no price view at all)

Anti-momentum losing least is not an edge — it traded fewest times. That is
the friction result restated, and it is exactly why ``run_study`` compares
against a matched-SIZE random basket.
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0).fillna(0)
    loss = (-delta.where(delta < 0, 0)).fillna(0)
    ag = gain.rolling(period, min_periods=period).mean()
    al = loss.rolling(period, min_periods=period).mean()
    return 100 - (100 / (1 + ag / (al + 1e-9)))


class Momentum20:
    """The CURRENT LIVE RULE: rank by 20-day price momentum.

    Selects the most extended names — the MBAPL failure mode (bought at
    RSI 94, reversed immediately). Baseline to beat, not a recommendation.
    """
    name = "momentum20"

    def rank(self, histories: Dict[str, pd.DataFrame], cutoff) -> Dict[str, float]:
        out = {}
        for sym, df in histories.items():
            c = df["Close"]
            if len(c) < 21:
                continue
            out[sym] = float(c.iloc[-1] / c.iloc[-21] - 1)
        return out


class PullbackInUptrend:
    """Established uptrend currently RESTING — 3-15% off its 20-day high,
    RSI 35-60, above a rising SMA50. Ranked by trend quality (60-day return)
    rather than by the recent pop."""
    name = "pullback_uptrend"

    def rank(self, histories: Dict[str, pd.DataFrame], cutoff) -> Dict[str, float]:
        out = {}
        for sym, df in histories.items():
            c = df["Close"]
            if len(c) < 61:
                continue
            sma50 = c.rolling(50).mean()
            if not (c.iloc[-1] > sma50.iloc[-1] and sma50.iloc[-1] > sma50.iloc[-21]):
                continue
            ret60 = c.iloc[-1] / c.iloc[-61] - 1
            if ret60 <= 0:
                continue
            drawdown = c.iloc[-1] / c.iloc[-20:].max() - 1
            if not (-0.15 <= drawdown <= -0.03):
                continue
            r = _rsi(c).iloc[-1]
            if not (np.isfinite(r) and 35.0 <= r <= 60.0):
                continue
            out[sym] = float(ret60)
        return out


class LowRsiUptrend:
    """Softer pullback variant: any rising-SMA50 uptrend, most oversold first."""
    name = "lowrsi_uptrend"

    def rank(self, histories: Dict[str, pd.DataFrame], cutoff) -> Dict[str, float]:
        out = {}
        for sym, df in histories.items():
            c = df["Close"]
            if len(c) < 71:
                continue
            sma50 = c.rolling(50).mean()
            if not (c.iloc[-1] > sma50.iloc[-1] and sma50.iloc[-1] > sma50.iloc[-21]):
                continue
            r = _rsi(c).iloc[-1]
            if np.isfinite(r):
                out[sym] = float(-r)          # negate: lower RSI ranks higher
        return out


class AntiMomentum:
    """CONTROL — the mirror of the live rule (worst 20-day performers)."""
    name = "anti_momentum"

    def rank(self, histories: Dict[str, pd.DataFrame], cutoff) -> Dict[str, float]:
        return {s: -v for s, v in Momentum20().rank(histories, cutoff).items()}


class LiquidityOnly:
    """CONTROL — no price view whatsoever; rank by traded value."""
    name = "liquidity_only"

    def rank(self, histories: Dict[str, pd.DataFrame], cutoff) -> Dict[str, float]:
        out = {}
        for sym, df in histories.items():
            if len(df) < 20:
                continue
            out[sym] = float(df["Volume"].iloc[-20:].mean() * df["Close"].iloc[-1])
        return out


CATALOGUE = {s.name: s for s in [
    Momentum20(), PullbackInUptrend(), LowRsiUptrend(), AntiMomentum(), LiquidityOnly(),
]}
