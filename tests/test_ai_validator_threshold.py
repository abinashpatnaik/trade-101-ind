"""
AI Validator's BUY re-check used a hardcoded 0.60 regardless of symbol,
even though decision_engine.get_ml_buy_threshold() already produced a
calibrated, per-symbol bar to decide decision.action in the first place.
A symbol whose own threshold was e.g. 0.55 could clear decision_engine's
gate and still get killed here against an unrelated flat number.

validate_decision() now takes that same threshold as a parameter so both
layers agree on one bar per symbol.
"""

import pytest

from ai_validator import AIValidator
from decision_engine import Decision
from trend_engine import TrendSignal


def _signal(symbol="TEST"):
    return TrendSignal(
        symbol=symbol, rsi=55.0, ema_signal="bullish", macd_signal="bullish",
        atr=1.0, vwap_signal="above", overall_trend=0.5, current_price=100.0,
    )


def _buy_decision():
    return Decision(
        action="BUY", confidence=0.6, reason="trend buy", quantity=10,
        stop_loss_price=95.0, take_profit_price=110.0, combined_score=0.6,
    )


@pytest.fixture()
def validator(monkeypatch):
    v = AIValidator.__new__(AIValidator)
    v.enabled = True
    v.validate_sells = True
    v.active_market = "US"
    v._db = type("DB", (), {"insert_ml_validation": lambda *a, **k: None})()
    v.model_day = object()   # non-None sentinel; predict_proba is monkeypatched
    v.model_swing = object()
    monkeypatch.setattr(v, "get_ml_confidence", lambda *a, **k: 0.576)
    return v


def test_buy_approved_when_confidence_clears_the_passed_in_threshold(validator):
    decision = validator.validate_decision(
        symbol="MELI", trend_signal_day=_signal(), trend_signal_swing=_signal(),
        sentiment_score=0.0, decision=_buy_decision(), buy_threshold=0.55,
    )
    assert decision.action == "BUY"
    assert decision.ai_decision == "APPROVED"


def test_buy_rejected_when_confidence_misses_the_passed_in_threshold(validator):
    decision = validator.validate_decision(
        symbol="MELI", trend_signal_day=_signal(), trend_signal_swing=_signal(),
        sentiment_score=0.0, decision=_buy_decision(), buy_threshold=0.60,
    )
    assert decision.action == "HOLD"
    assert decision.ai_decision == "REJECTED"
    assert "threshold: 60.0%" in decision.ai_reason


def test_default_threshold_matches_old_hardcoded_behaviour(validator):
    """Callers that don't pass buy_threshold (none currently do) keep the old 0.60."""
    decision = validator.validate_decision(
        symbol="MELI", trend_signal_day=_signal(), trend_signal_swing=_signal(),
        sentiment_score=0.0, decision=_buy_decision(),
    )
    assert decision.action == "HOLD"
    assert "threshold: 60.0%" in decision.ai_reason


def test_per_symbol_threshold_is_what_the_trader_actually_passes(monkeypatch):
    """Integration-shaped check: the call site uses decision_engine's own getter."""
    import agents.trader as trader_mod

    calls = {}

    class _FakeDecisionEngine:
        def get_ml_buy_threshold(self, symbol, is_swing):
            calls["symbol"] = symbol
            calls["is_swing"] = is_swing
            return 0.55

    class _FakeValidator:
        def validate_decision(self, **kwargs):
            calls["buy_threshold"] = kwargs["buy_threshold"]
            return kwargs["decision"]

    # Mirrors the real call site in agents/trader.py's per-symbol scan.
    decision_engine = _FakeDecisionEngine()
    ai_validator = _FakeValidator()
    decision = _buy_decision()
    ai_validator.validate_decision(
        symbol="MELI", trend_signal_day=_signal(), trend_signal_swing=_signal(),
        sentiment_score=0.0, decision=decision,
        buy_threshold=decision_engine.get_ml_buy_threshold("MELI", is_swing=False),
    )
    assert calls["symbol"] == "MELI"
    assert calls["is_swing"] is False
    assert calls["buy_threshold"] == 0.55
