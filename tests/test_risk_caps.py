"""
Fixed-fraction risk cap + weekly loss kill-switch.

Both come from the two strategy documents' one shared, actionable demand:
"cap risk hard". Neither creates edge — the cap equalises what a single stop-out
can cost (volatile names no longer risk 2-3x more than calm ones), and the
kill-switch stops paying friction when a week diverges badly from the ~zero
expectancy the backtests measured.

Defaults are 0 (disabled) so behaviour is unchanged unless a market opts in
via docker-compose.
"""

import pytest

from decision_engine import DecisionEngine
from trend_engine import TrendSignal


def _signal(price=100.0, atr=1.0):
    return TrendSignal(
        symbol="AAPL", rsi=55.0, ema_signal="bullish", macd_signal="bullish",
        vwap_signal="above", overall_trend=0.8, atr=atr, current_price=price,
        adx=30.0, volume_ratio=2.0,
    )


@pytest.fixture()
def engine():
    return DecisionEngine()


# ------------------------------------------------------------- defaults
def test_defaults_disabled():
    from config import config
    assert getattr(config.risk, "max_risk_per_trade_pct", 0.0) == 0.0
    assert getattr(config.risk, "max_weekly_loss_pct", 0.0) == 0.0


def test_cap_disabled_leaves_quantity_untouched(engine, monkeypatch):
    monkeypatch.setattr(engine._risk, "max_risk_per_trade_pct", 0.0, raising=False)
    assert engine._apply_risk_cap(37.0, 100_000.0, 100.0, 1.0) == 37.0


# ------------------------------------------------------------- the cap
def test_cap_limits_loss_at_stop(engine, monkeypatch):
    """qty × price × stop_pct must not exceed equity × cap."""
    monkeypatch.setattr(engine._risk, "max_risk_per_trade_pct", 0.005, raising=False)
    equity, price, atr = 100_000.0, 100.0, 1.0
    stop_pct = engine.dynamic_stop_pct(price, atr)
    qty = engine._apply_risk_cap(1_000.0, equity, price, atr)
    assert qty < 1_000.0
    assert qty * price * stop_pct <= equity * 0.005 + 1e-6


def test_cap_never_increases_size(engine, monkeypatch):
    monkeypatch.setattr(engine._risk, "max_risk_per_trade_pct", 0.005, raising=False)
    # tiny existing quantity stays as-is even though the budget allows more
    assert engine._apply_risk_cap(1.0, 100_000.0, 100.0, 1.0) == 1.0


def test_volatile_names_get_smaller_positions(engine, monkeypatch):
    """The point of risk sizing: same rupee risk => fewer shares of the wild one."""
    monkeypatch.setattr(engine._risk, "max_risk_per_trade_pct", 0.005, raising=False)
    calm = engine._apply_risk_cap(10_000.0, 100_000.0, 100.0, atr=0.5)
    wild = engine._apply_risk_cap(10_000.0, 100_000.0, 100.0, atr=2.5)
    assert wild < calm


def test_cap_floors_whole_shares_for_india(engine, monkeypatch):
    """IN quantities are whole shares and must round DOWN, never up."""
    monkeypatch.setattr("decision_engine.ACTIVE_MARKET", "IN")
    monkeypatch.setattr(engine._risk, "max_risk_per_trade_pct", 0.005, raising=False)
    qty = engine._apply_risk_cap(100.0, 7_554.0, 280.0, atr=3.5)
    assert qty == float(int(qty))
    stop_pct = engine.dynamic_stop_pct(280.0, 3.5)
    assert qty * 280.0 * stop_pct <= 7_554.0 * 0.005 + 1e-6


def test_dynamic_stop_matches_bounds(engine):
    """Helper reproduces the live stop rule: max(floor, min(5%, 2*ATR/price))."""
    assert engine.dynamic_stop_pct(100.0, 0.5) == pytest.approx(
        max(engine._risk.stop_loss_pct, 0.01))
    assert engine.dynamic_stop_pct(100.0, 10.0) == 0.05          # capped at 5%
    assert engine.dynamic_stop_pct(100.0, 0.0) == engine._risk.stop_loss_pct


# --------------------------------------------------- weekly kill-switch
def test_weekly_block_reason_has_priority():
    from agents.trader import TradingAgent
    r = TradingAgent._buy_block_reason(True, False, weekly_paused=True)
    assert "weekly loss" in r.lower()
    r2 = TradingAgent._buy_block_reason(True, False, weekly_paused=False)
    assert "close" in r2.lower()                                  # unchanged
    r3 = TradingAgent._buy_block_reason(False, False, weekly_paused=True)
    assert r3 == "Not in today's approved targets — exit-only"    # off-list wins


def test_weekly_pause_thresholds(monkeypatch):
    from agents.trader import TradingAgent
    agent = TradingAgent.__new__(TradingAgent)

    class _DB:
        def __init__(self, pnl):
            self._pnl = pnl

        def get_realized_pnl(self, since_date=None, mode=None, net=False):
            assert net is True, "kill-switch must measure NET, not gross"
            return {"realizedPnl": self._pnl, "sellCount": 5, "unknownCount": 0}

    class _P:
        portfolio_value = 10_000.0

    agent.portfolio = _P()
    import agents.trader as trader_mod
    monkeypatch.setattr(trader_mod.config.risk, "max_weekly_loss_pct", 0.04,
                        raising=False)

    agent._trading_db = _DB(-500.0)        # -5% of NAV -> paused
    assert agent._weekly_loss_paused() is True
    agent._trading_db = _DB(-100.0)        # -1% -> fine
    assert agent._weekly_loss_paused() is False
    agent._trading_db = _DB(-400.0)        # exactly -4% -> paused (<=)
    assert agent._weekly_loss_paused() is True


def test_weekly_pause_disabled_and_fails_open(monkeypatch):
    from agents.trader import TradingAgent
    import agents.trader as trader_mod
    agent = TradingAgent.__new__(TradingAgent)

    monkeypatch.setattr(trader_mod.config.risk, "max_weekly_loss_pct", 0.0,
                        raising=False)
    assert agent._weekly_loss_paused() is False     # disabled: no DB touched

    monkeypatch.setattr(trader_mod.config.risk, "max_weekly_loss_pct", 0.04,
                        raising=False)

    class _Boom:
        def get_realized_pnl(self, **kw):
            raise RuntimeError("db unavailable")

    class _P:
        portfolio_value = 10_000.0

    agent.portfolio = _P()
    agent._trading_db = _Boom()
    assert agent._weekly_loss_paused() is False     # fail OPEN with a warning


# --------------------------------------------------- manual entries-only halt
def test_no_new_entries_reason_names_itself_not_the_weekly_cap(monkeypatch):
    """The manual halt reuses weekly_paused's gating path, so it must not
    blame a weekly cap that was never breached — that misreads on the
    dashboard as a risk event rather than a deliberate halt."""
    from agents import trader as trader_mod
    from agents.trader import TradingAgent

    # Patch the config object trader actually holds a reference to, not a
    # freshly imported one — they are not necessarily the same instance.
    monkeypatch.setattr(trader_mod.config.agent, "no_new_entries", True,
                        raising=False)
    r = TradingAgent._buy_block_reason(True, False, weekly_paused=True)
    assert "no_new_entries" in r.lower()
    assert "weekly" not in r.lower()
    # Off-list names are still reported as off-list.
    assert TradingAgent._buy_block_reason(False, False, weekly_paused=True) == \
        "Not in today's approved targets — exit-only"


def test_no_new_entries_defaults_off(monkeypatch):
    """Absent the env var the switch must be inert — a stale default that
    silently halts a live account is worse than one that trades.

    Builds a fresh AgentConfig rather than reloading the module: a reload
    rebinds config.config and leaves agents.trader pointing at the old
    instance, which silently breaks other tests.
    """
    from config import AgentConfig

    monkeypatch.delenv("NO_NEW_ENTRIES", raising=False)
    assert AgentConfig().no_new_entries is False
    monkeypatch.setenv("NO_NEW_ENTRIES", "true")
    assert AgentConfig().no_new_entries is True


def test_no_new_entries_never_gates_an_exit():
    """Structural guarantee: the flag is read ONLY on the entry path.

    OBSERVE_ONLY mutes exits too (stops, gap-down, EOD flatten). If
    no_new_entries ever leaked into one of those branches, a halted account
    would sit on unmanaged positions — the exact failure this switch exists
    to avoid.
    """
    import inspect

    from agents import trader as trader_mod

    src = inspect.getsource(trader_mod)
    for line in src.splitlines():
        if "no_new_entries" in line and not line.strip().startswith("#"):
            lowered = line.lower()
            assert "close_position" not in lowered
            assert "exit" not in lowered or "exits" in lowered
