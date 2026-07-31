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


class RocketIgnition:
    """HYPOTHESIS (untested): catch a move already igniting, not predict a
    quiet name. Encodes the user's "rocket" definition on point-in-time daily
    bars — every value uses only the last COMPLETED bar as-of the cutoff, so
    it is lookahead-safe by construction:

      * range expansion — the last bar's High-Low is >= ``range_mult`` x the
        10-day average true range measured BEFORE that bar (today excluded);
      * relative volume — the last bar's volume is >= ``vol_mult`` x the prior
        20-day average;
      * structure — the close breaks the prior ``breakout_lookback``-day high
        (base/last-N-bars high, today excluded) AND finishes in the upper
        ``close_strength`` of its own range (it held the move, not a spike-fade).

    Only names clearing ALL THREE get a score; among them the rank is
    ``range_expansion x relative_volume`` so the most decisive ignition sorts
    first. This is a REFERENCE HYPOTHESIS, not a recommendation — no gross
    edge has been shown for any selection rule here; the point is to let
    ``run_study``'s matched-random control decide.
    """
    name = "rocket_ignition"

    def __init__(self, range_mult: float = 1.5, vol_mult: float = 1.5,
                 breakout_lookback: int = 20, close_strength: float = 0.5):
        self.range_mult = range_mult
        self.vol_mult = vol_mult
        self.breakout_lookback = breakout_lookback
        self.close_strength = close_strength

    def rank(self, histories: Dict[str, pd.DataFrame], cutoff) -> Dict[str, float]:
        out: Dict[str, float] = {}
        need = self.breakout_lookback + 2
        for sym, df in histories.items():
            if len(df) < need:
                continue
            h, l, c, v = df["High"], df["Low"], df["Close"], df["Volume"]

            # True range series, then the 10-day ATR baseline that EXCLUDES the
            # last bar (so the bar can be compared against its own history).
            prev_c = c.shift(1)
            tr = np.maximum(h - l, np.maximum((h - prev_c).abs(), (l - prev_c).abs()))
            baseline_atr = float(tr.iloc[-11:-1].mean())
            if not np.isfinite(baseline_atr) or baseline_atr <= 0:
                continue
            last_range = float(h.iloc[-1] - l.iloc[-1])
            range_expansion = last_range / baseline_atr
            if range_expansion < self.range_mult:
                continue

            avg_vol = float(v.iloc[-(self.breakout_lookback + 1):-1].mean())
            if not np.isfinite(avg_vol) or avg_vol <= 0:
                continue
            rel_vol = float(v.iloc[-1]) / avg_vol
            if rel_vol < self.vol_mult:
                continue

            prior_high = float(h.iloc[-(self.breakout_lookback + 1):-1].max())
            if not (float(c.iloc[-1]) > prior_high):
                continue

            if last_range <= 0:
                continue
            close_pos = (float(c.iloc[-1]) - float(l.iloc[-1])) / last_range
            if close_pos < self.close_strength:
                continue

            out[sym] = range_expansion * rel_vol
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
    Momentum20(), PullbackInUptrend(), LowRsiUptrend(), RocketIgnition(),
    AntiMomentum(), LiquidityOnly(),
]}
