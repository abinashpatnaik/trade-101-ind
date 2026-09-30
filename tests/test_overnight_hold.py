"""
Overnight-hold gate: markets can opt out of carrying positions into delivery.

Measured on 50 live IN round trips (2026-07-07..07-21), positions carried
overnight lost -Rs31.82/trade against -Rs7.65 for same-day exits, and two gap
exits were half the period's entire loss. IN therefore flattens at the close.

US must keep the hold: flattening every session would burn the sub-$25K PDT
day-trade budget that agents.pdt_guard exists to protect, so the config default
stays True and only IN opts out (via docker-compose).
"""

import importlib
import os

import pytest

from agents.trader import TradingAgent


def _reload_config(monkeypatch, value):
    """Re-import config with ALLOW_OVERNIGHT_HOLD set, returning the module."""
    if value is None:
        monkeypatch.delenv("ALLOW_OVERNIGHT_HOLD", raising=False)
    else:
        monkeypatch.setenv("ALLOW_OVERNIGHT_HOLD", value)
    import config as config_module
    return importlib.reload(config_module)


@pytest.mark.parametrize("value,expected", [
    (None, True),        # unset -> unchanged behaviour (US keeps its hold)
    ("true", True),
    ("false", False),
    ("FALSE", False),
])
def test_flag_parsing(monkeypatch, value, expected):
    cfg = _reload_config(monkeypatch, value)
    assert cfg.config.risk.allow_overnight_hold is expected


def test_default_is_permissive(monkeypatch):
    """A missing env var must not silently start flattening the US book."""
    cfg = _reload_config(monkeypatch, None)
    assert cfg.config.risk.allow_overnight_hold is True


class _StubExecutor:
    def __init__(self):
        self.closed = []

    def close_position(self, symbol, qty):
        self.closed.append((symbol, qty))
        return True

    def pop_fill_price(self, symbol):
        return None


def _agent_with_position(monkeypatch, allow_overnight):
    """A TradingAgent stubbed down to just what close_all_positions touches."""
    agent = TradingAgent.__new__(TradingAgent)

    import agents.trader as trader_mod
    monkeypatch.setattr(trader_mod.config.risk, "allow_overnight_hold",
                        allow_overnight, raising=False)
    monkeypatch.setattr(trader_mod.config.agent, "observe_only", False, raising=False)

    class _Portfolio:
        is_simulated = False
        open_positions = {"BFINVEST.NS": {"quantity": 4.0, "avg_cost": 541.0}}

        def set_pending_reason(self, *a, **k):
            pass

    class _Feed:
        def get_current_price(self, symbol):
            return 474.10

    import threading
    agent.portfolio = _Portfolio()
    agent.price_feed = _Feed()
    agent.executor = _StubExecutor()
    agent._positions_lock = threading.Lock()
    # The swing model would vote to hold this one.
    agent._evaluate_ml_hold = lambda symbol: True
    agent.learning = type("L", (), {"on_trade_closed": lambda *a, **k: None})()
    agent.sentiment_engine = type("S", (), {"get_last_headlines": lambda *a, **k: []})()
    return agent


def test_hold_is_honoured_when_allowed(monkeypatch):
    """US path: the swing model's conviction still carries the position."""
    agent = _agent_with_position(monkeypatch, allow_overnight=True)
    agent.close_all_positions(reason="EOD")
    assert agent.executor.closed == [], "position should have been held overnight"


def test_hold_is_overridden_when_disallowed(monkeypatch):
    """IN path: conviction is ignored and the position is flattened."""
    agent = _agent_with_position(monkeypatch, allow_overnight=False)
    agent.close_all_positions(reason="EOD")
    assert agent.executor.closed == [("BFINVEST.NS", 4.0)]


def test_non_eod_close_is_unaffected(monkeypatch):
    """SHUTDOWN/other reasons never consulted the hold and still don't."""
    agent = _agent_with_position(monkeypatch, allow_overnight=True)
    agent.close_all_positions(reason="SHUTDOWN")
    assert agent.executor.closed == [("BFINVEST.NS", 4.0)]


# --- Losers-only carve-out: hold_losing_swing_threshold (2026-09-30) -------
#
# Separate from allow_overnight_hold above: even on a market that flattens
# by default (IN), a LOSING position may still be held overnight if swing
# confidence clears this lower, dedicated bar.


def _agent_with_losing_position(monkeypatch, hold_losing_swing_threshold, ml_hold_return):
    agent = TradingAgent.__new__(TradingAgent)

    import agents.trader as trader_mod
    monkeypatch.setattr(trader_mod.config.risk, "allow_overnight_hold", False, raising=False)
    monkeypatch.setattr(
        trader_mod.config.risk, "hold_losing_swing_threshold",
        hold_losing_swing_threshold, raising=False,
    )
    monkeypatch.setattr(trader_mod.config.agent, "observe_only", False, raising=False)

    class _Portfolio:
        is_simulated = False
        # avg_cost 541.0, current price 474.10 below -> a real loser.
        open_positions = {"BFINVEST.NS": {"quantity": 4.0, "avg_cost": 541.0}}

        def set_pending_reason(self, *a, **k):
            pass

    class _Feed:
        def get_current_price(self, symbol):
            return 474.10

    import threading
    agent.portfolio = _Portfolio()
    agent.price_feed = _Feed()
    agent.executor = _StubExecutor()
    agent._positions_lock = threading.Lock()
    agent._evaluate_ml_hold = lambda symbol, threshold=0.65, apply_sentiment_discount=True: ml_hold_return
    agent.learning = type("L", (), {"on_trade_closed": lambda *a, **k: None})()
    agent.sentiment_engine = type("S", (), {"get_last_headlines": lambda *a, **k: []})()
    return agent


def test_losing_position_held_when_swing_clears_threshold(monkeypatch):
    agent = _agent_with_losing_position(
        monkeypatch, hold_losing_swing_threshold=0.45, ml_hold_return=True,
    )
    agent.close_all_positions(reason="EOD")
    assert agent.executor.closed == [], "losing position should have been held overnight"


def test_losing_position_closed_when_swing_misses_threshold(monkeypatch):
    agent = _agent_with_losing_position(
        monkeypatch, hold_losing_swing_threshold=0.45, ml_hold_return=False,
    )
    agent.close_all_positions(reason="EOD")
    assert agent.executor.closed == [("BFINVEST.NS", 4.0)]


def test_losing_position_closed_when_carveout_disabled(monkeypatch):
    """Threshold 0.0 (the default) is fully off, regardless of swing confidence."""
    agent = _agent_with_losing_position(
        monkeypatch, hold_losing_swing_threshold=0.0, ml_hold_return=True,
    )
    agent.close_all_positions(reason="EOD")
    assert agent.executor.closed == [("BFINVEST.NS", 4.0)]


def test_winning_position_ignores_losing_carveout(monkeypatch):
    """The losers-only bar must never fire for a position that isn't down."""
    agent = TradingAgent.__new__(TradingAgent)

    import agents.trader as trader_mod
    monkeypatch.setattr(trader_mod.config.risk, "allow_overnight_hold", False, raising=False)
    monkeypatch.setattr(
        trader_mod.config.risk, "hold_losing_swing_threshold", 0.45, raising=False,
    )
    monkeypatch.setattr(trader_mod.config.agent, "observe_only", False, raising=False)

    class _Portfolio:
        is_simulated = False
        # avg_cost 400.0, current price 450.0 above -> a winner, not a loser.
        open_positions = {"AAPL": {"quantity": 2.0, "avg_cost": 400.0}}

        def set_pending_reason(self, *a, **k):
            pass

    class _Feed:
        def get_current_price(self, symbol):
            return 450.0

    import threading
    agent.portfolio = _Portfolio()
    agent.price_feed = _Feed()
    agent.executor = _StubExecutor()
    agent._positions_lock = threading.Lock()
    agent._evaluate_ml_hold = lambda symbol, threshold=0.65, apply_sentiment_discount=True: True
    agent.learning = type("L", (), {"on_trade_closed": lambda *a, **k: None})()
    agent.sentiment_engine = type("S", (), {"get_last_headlines": lambda *a, **k: []})()

    agent.close_all_positions(reason="EOD")
    assert agent.executor.closed == [("AAPL", 2.0)]


# --- _evaluate_ml_hold: threshold / apply_sentiment_discount params --------


def _agent_for_ml_hold(monkeypatch, ml_confidence, sentiment_score=0.0):
    agent = TradingAgent.__new__(TradingAgent)

    import agents.trader as trader_mod
    monkeypatch.setattr(trader_mod.config.ai, "enabled", True, raising=False)

    import pandas as pd

    agent.ai_validator = type("AIV", (), {
        "enabled": True,
        "get_ml_confidence": lambda self, trend_signal, sentiment, mode: ml_confidence,
    })()
    agent.price_feed = type("Feed", (), {
        "get_daily_ohlcv": lambda self, symbol, period="3mo": pd.DataFrame({"close": [1, 2, 3]}),
    })()
    agent.trend_engine = type("Trend", (), {
        "analyse": lambda self, symbol, df: object(),
    })()
    agent.sentiment_engine = type("Sent", (), {
        "get_sentiment": lambda self, symbol: sentiment_score,
    })()
    return agent


def test_ml_hold_uses_flat_threshold_without_sentiment_discount(monkeypatch):
    """The losers-only call path: no discount, confidence must clear 0.45 exactly."""
    agent = _agent_for_ml_hold(monkeypatch, ml_confidence=0.46, sentiment_score=0.8)
    assert agent._evaluate_ml_hold(
        "X", threshold=0.45, apply_sentiment_discount=False,
    ) is True

    agent = _agent_for_ml_hold(monkeypatch, ml_confidence=0.44, sentiment_score=0.8)
    assert agent._evaluate_ml_hold(
        "X", threshold=0.45, apply_sentiment_discount=False,
    ) is False


def test_ml_hold_default_args_reproduce_original_behaviour(monkeypatch):
    """Unchanged call site (no kwargs): 0.65 base, sentiment discount applies."""
    # Sentiment 1.0 discounts the full 0.15, so 0.51 clears the resulting 0.50.
    agent = _agent_for_ml_hold(monkeypatch, ml_confidence=0.51, sentiment_score=1.0)
    assert agent._evaluate_ml_hold("X") is True

    # No sentiment -> threshold stays at 0.65; 0.60 falls short.
    agent = _agent_for_ml_hold(monkeypatch, ml_confidence=0.60, sentiment_score=0.0)
    assert agent._evaluate_ml_hold("X") is False
