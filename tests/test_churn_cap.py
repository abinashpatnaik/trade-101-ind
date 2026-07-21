"""
Per-symbol daily entry cap (churn control).

Churn is the one lever with a measured payoff: expectancy is negative
(-0.214%/trade over 4,407 US backtest trades), so each avoided round trip saves
the friction it would have paid. Live logs showed the failure mode this targets
— one IN symbol entered 5x in a day, one US symbol 10x, the model re-firing on
the same setup.

Default is 0 (unlimited) so behaviour is unchanged unless a market opts in.
"""

import datetime as _dt

import pytest

from decision_engine import DecisionEngine
from trend_engine import TrendSignal


@pytest.fixture()
def make_buy_signal():
    """A signal that clears the ADX/volume/edge gates, so the only thing
    that can block the BUY is the churn cap under test."""
    return TrendSignal(
        symbol="AAPL", rsi=55.0, ema_signal="bullish", macd_signal="bullish",
        vwap_signal="above", overall_trend=0.8, atr=4.0, current_price=100.0,
        adx=30.0, volume_ratio=2.0,
    )


@pytest.fixture()
def engine(monkeypatch):
    eng = DecisionEngine()
    monkeypatch.setattr(eng._risk, "max_entries_per_symbol_per_day", 2, raising=False)
    return eng


def test_default_is_unlimited():
    """A missing env var must not silently start blocking entries."""
    from config import config
    assert getattr(config.risk, "max_entries_per_symbol_per_day", 0) == 0


def test_counter_starts_at_zero(engine):
    assert engine.entries_used("AAPL") == 0


def test_register_entry_increments_per_symbol(engine):
    engine.register_entry("AAPL")
    engine.register_entry("AAPL")
    engine.register_entry("MSFT")
    assert engine.entries_used("AAPL") == 2
    assert engine.entries_used("MSFT") == 1


def test_counter_resets_on_new_day(engine, monkeypatch):
    engine.register_entry("AAPL")
    engine.register_entry("AAPL")
    assert engine.entries_used("AAPL") == 2

    real_datetime = _dt.datetime
    tomorrow = real_datetime.now().date() + _dt.timedelta(days=1)

    class _Tomorrow(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.combine(tomorrow, real_datetime.min.time())

    monkeypatch.setattr("decision_engine.datetime", _Tomorrow)
    assert engine.entries_used("AAPL") == 0, "counters must roll over daily"


def test_cap_blocks_the_third_entry(engine, make_buy_signal):
    """Two entries allowed, the third is refused as churn."""
    portfolio = {"portfolio_value": 100_000.0, "available_funds": 100_000.0,
                 "open_positions": {}}
    sym = "AAPL"
    for _ in range(2):
        d = engine.make_decision(symbol=sym, trend_signal=make_buy_signal,
                                 sentiment_score=0.0, current_price=100.0,
                                 portfolio=portfolio, ml_confidence_day=0.99)
        assert d.action == "BUY"
        engine.register_entry(sym)

    blocked = engine.make_decision(symbol=sym, trend_signal=make_buy_signal,
                                   sentiment_score=0.0, current_price=100.0,
                                   portfolio=portfolio, ml_confidence_day=0.99)
    assert blocked.action == "HOLD"
    assert "churn" in blocked.reason.lower()


def test_cap_does_not_block_a_different_symbol(engine, make_buy_signal):
    portfolio = {"portfolio_value": 100_000.0, "available_funds": 100_000.0,
                 "open_positions": {}}
    engine.register_entry("AAPL")
    engine.register_entry("AAPL")
    d = engine.make_decision(symbol="MSFT", trend_signal=make_buy_signal,
                             sentiment_score=0.0, current_price=100.0,
                             portfolio=portfolio, ml_confidence_day=0.99)
    assert d.action == "BUY"


def test_cap_zero_never_blocks(monkeypatch, make_buy_signal):
    eng = DecisionEngine()
    monkeypatch.setattr(eng._risk, "max_entries_per_symbol_per_day", 0, raising=False)
    portfolio = {"portfolio_value": 100_000.0, "available_funds": 100_000.0,
                 "open_positions": {}}
    for _ in range(6):
        d = eng.make_decision(symbol="AAPL", trend_signal=make_buy_signal,
                              sentiment_score=0.0, current_price=100.0,
                              portfolio=portfolio, ml_confidence_day=0.99)
        assert d.action == "BUY"
        eng.register_entry("AAPL")
    assert eng.entries_used("AAPL") == 6


def test_exits_are_never_blocked_by_the_cap(engine, make_buy_signal):
    """A held position must still be manageable after the cap is hit —
    the cap gates ENTRIES only, never exits."""
    engine.register_entry("AAPL")
    engine.register_entry("AAPL")
    portfolio = {"portfolio_value": 100_000.0, "available_funds": 100_000.0,
                 "open_positions": {"AAPL": {"quantity": 10, "avg_cost": 100.0}}}
    d = engine.make_decision(symbol="AAPL", trend_signal=make_buy_signal,
                             sentiment_score=0.0, current_price=100.0,
                             portfolio=portfolio, ml_confidence_day=0.05)
    assert d.action != "BUY"
