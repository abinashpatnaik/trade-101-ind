"""Directive parity: an empty/absent directive must produce the exact same
Decision as the pre-refactor engine (config-driven constants)."""

import pytest

from decision_engine import DecisionEngine
from trend_engine import TrendSignal


def _signal(**overrides):
    base = dict(
        symbol="RELIANCE.NS",
        rsi=55.0,
        ema_signal="bullish",
        macd_signal="bullish",
        vwap_signal="above",
        overall_trend=0.8,
        atr=20.0,
        current_price=2000.0,
        adx=30.0,
        volume_ratio=2.0,
    )
    base.update(overrides)
    return TrendSignal(**base)


def _portfolio(**overrides):
    base = dict(
        portfolio_value=100_000.0,
        available_funds=100_000.0,
        open_positions={},
    )
    base.update(overrides)
    return base


@pytest.fixture
def engine():
    return DecisionEngine()


def _decide(engine, **kwargs):
    defaults = dict(
        symbol="RELIANCE.NS",
        trend_signal=_signal(),
        sentiment_score=0.5,
        current_price=2000.0,
        portfolio=_portfolio(),
    )
    defaults.update(kwargs)
    return engine.make_decision(**defaults)


def test_empty_directive_is_identity(engine):
    baseline = _decide(engine)
    engine.apply_directive({})
    with_empty = _decide(engine)
    assert baseline == with_empty


def test_cleared_directive_restores_baseline(engine):
    baseline = _decide(engine)
    engine.apply_directive({"buy_threshold": 0.99, "position_size_multiplier": 0.5})
    changed = _decide(engine)
    assert changed != baseline  # directive actually did something
    engine.clear_directive()
    restored = _decide(engine)
    assert restored == baseline


def test_unknown_keys_ignored(engine):
    baseline = _decide(engine)
    engine.apply_directive({"bogus_key": 123, "another": "x"})
    assert _decide(engine) == baseline


def test_buy_threshold_override_blocks_buy(engine):
    baseline = _decide(engine)
    assert baseline.action == "BUY"
    engine.apply_directive({"buy_threshold": 0.99})
    assert _decide(engine).action == "HOLD"


def test_position_size_multiplier_halves_quantity(engine):
    baseline = _decide(engine)
    assert baseline.action == "BUY"
    engine.apply_directive({"position_size_multiplier": 0.5})
    halved = _decide(engine)
    assert halved.action == "BUY"
    assert halved.quantity == pytest.approx(baseline.quantity * 0.5, abs=1)


def test_sniper_adx_gate_override(engine):
    # ADX 27 passes the default gate (25) but fails a raised gate (30)
    sig = _signal(adx=27.0)
    assert _decide(engine, trend_signal=sig).action == "BUY"
    engine.apply_directive({"sniper_min_adx": 30})
    blocked = _decide(engine, trend_signal=sig)
    assert blocked.action == "HOLD"
    assert "ADX" in blocked.reason


def test_max_open_positions_override(engine):
    positions = {
        "TCS.NS": {"quantity": 1, "avg_cost": 100.0, "market_value": 100.0},
        "INFY.NS": {"quantity": 1, "avg_cost": 100.0, "market_value": 100.0},
    }
    # Default max is 3 → third position allowed
    assert _decide(engine, portfolio=_portfolio(open_positions=positions)).action == "BUY"
    engine.apply_directive({"max_open_positions": 2})
    capped = _decide(engine, portfolio=_portfolio(open_positions=positions))
    assert capped.action == "HOLD"
    assert "max open positions" in capped.reason


def test_ml_threshold_delta(engine):
    base_thr = engine.get_ml_buy_threshold("UNKNOWN_SYM", is_swing=False)
    engine.apply_directive({"ml_buy_threshold_delta": 0.08})
    assert engine.get_ml_buy_threshold("UNKNOWN_SYM", is_swing=False) == pytest.approx(base_thr + 0.08)
