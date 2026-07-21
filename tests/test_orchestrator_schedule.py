"""Orchestrator job scheduling — the pre-open trader restart.

Regression cover for the live-price outage: Zerodha access tokens expire each
morning, and a trader process that spans the rollover loses its KiteTicker
websocket permanently (kiteconnect runs on Twisted, whose reactor cannot be
restarted in-process). The orchestrator must restart the trader before the open
so each session starts with a fresh token and a working tick feed.
"""

import logging
import types

from agents.orchestrator import OrchestratorAgent
from config import config


def _stub_orchestrator(secs_to_open: float):
    """An OrchestratorAgent with __init__ bypassed (it would need a live Bus)."""
    o = OrchestratorAgent.__new__(OrchestratorAgent)
    o.config = config
    o.logger = logging.getLogger("test-orchestrator")
    o.session = types.SimpleNamespace(
        seconds_to_open=lambda: secs_to_open,
        get_session_date=lambda: "2026-07-21",
    )
    o._last_strategy_cmd = 0.0
    o._last_intraday_cmd = 0.0

    marks = set()
    o._sched_marker_is_today = lambda job: job in marks
    o._mark_sched = marks.add
    o._send_cmd = lambda *a, **k: None

    restarted = []
    o._restart_agent_container = lambda agent, reason: (restarted.append(agent), True)[1]
    return o, restarted


def test_preopen_trader_restart_fires_once_inside_window():
    window = config.orchestrator.trader_restart_minutes_before_open * 60
    o, restarted = _stub_orchestrator(secs_to_open=window - 60)  # just inside

    o._schedule_jobs("PRE_MARKET")
    assert restarted == ["trader"]

    # Idempotent — the daily marker must prevent a restart loop.
    o._schedule_jobs("PRE_MARKET")
    assert restarted == ["trader"]


def test_no_trader_restart_far_from_open():
    window = config.orchestrator.trader_restart_minutes_before_open * 60
    o, restarted = _stub_orchestrator(secs_to_open=window + 3600)  # well outside
    o._schedule_jobs("CLOSED")
    assert restarted == []


def test_no_trader_restart_while_market_open():
    """Never restart mid-session — that would disturb open positions."""
    o, restarted = _stub_orchestrator(secs_to_open=0)
    o._schedule_jobs("OPEN")
    assert restarted == []
