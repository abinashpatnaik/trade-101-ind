"""Regime classifier and hysteresis tests (pure functions, no market data)."""

import numpy as np
import pandas as pd

from agents.strategy import (
    Hysteresis,
    classify_regime,
    compute_index_indicators,
    REGIME_DIRECTIVES,
)
from decision_engine import DecisionEngine


def test_volatile_by_absolute_atr():
    assert classify_regime(adx=30, atr_pct=0.03, atr_pct_p90=0.05, ema20_slope=0.002) == "VOLATILE"


def test_volatile_by_percentile():
    # ATR% below the absolute cutoff but above its own trailing p90
    assert classify_regime(adx=30, atr_pct=0.015, atr_pct_p90=0.012, ema20_slope=0.002) == "VOLATILE"


def test_trending():
    assert classify_regime(adx=30, atr_pct=0.01, atr_pct_p90=0.02, ema20_slope=0.002) == "TRENDING"


def test_trending_requires_slope():
    # High ADX but a flat EMA20 → not a usable trend
    assert classify_regime(adx=30, atr_pct=0.01, atr_pct_p90=0.02, ema20_slope=0.0001) == "RANGING"


def test_ranging():
    assert classify_regime(adx=15, atr_pct=0.01, atr_pct_p90=0.02, ema20_slope=0.0002) == "RANGING"


def test_hysteresis_requires_consecutive_reads():
    h = Hysteresis(reads_required=2, initial="RANGING")
    assert h.update("TRENDING") == "RANGING"   # 1st disagreeing read — hold
    assert h.update("TRENDING") == "TRENDING"  # 2nd consecutive — switch


def test_hysteresis_resets_on_flapping():
    h = Hysteresis(reads_required=2, initial="RANGING")
    h.update("TRENDING")                        # candidate TRENDING x1
    assert h.update("VOLATILE") == "RANGING"    # new candidate — counter resets
    assert h.update("VOLATILE") == "VOLATILE"   # x2 — now switches


def test_directives_are_valid_decision_engine_keys():
    for regime, params in REGIME_DIRECTIVES.items():
        unknown = set(params) - DecisionEngine.DIRECTIVE_KEYS
        assert not unknown, f"{regime} has unknown directive keys: {unknown}"


def _ohlcv(closes, spread=0.001):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes * (1 + spread),
            "Low": closes * (1 - spread),
            "Close": closes,
            "Volume": np.full(len(closes), 1000.0),
        },
        index=idx,
    )


def test_indicators_uptrend_has_positive_slope_and_adx():
    n = 90
    closes = 100 * (1 + np.linspace(0, 0.15, n))  # steady +15%
    daily = _ohlcv(closes)
    intraday = _ohlcv(closes)  # shape is what matters for the ADX math
    ind = compute_index_indicators(intraday, daily)
    assert ind["ema20_slope"] > 0
    assert ind["adx"] > 25
    assert ind["atr_pct"] > 0


def test_indicators_flat_market_low_adx():
    n = 90
    rng = np.random.RandomState(3)
    closes = 100 * (1 + 0.001 * np.sin(np.arange(n) / 4) + rng.normal(0, 0.0003, n))
    ind = compute_index_indicators(_ohlcv(closes), _ohlcv(closes))
    assert ind["adx"] < 25
    assert abs(ind["ema20_slope"]) < 0.0005
