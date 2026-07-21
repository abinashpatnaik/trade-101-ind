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
