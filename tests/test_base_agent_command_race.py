"""
BaseAgent.run() used to start the command-channel listener BEFORE calling
setup() -- so a command arriving in that window reached on_command() while
state setup() initializes (locks, sessions, clients) didn't exist yet.

Confirmed live 2026-10-07: us-scanner crashed with
    AttributeError: 'ScannerAgent' object has no attribute '_scan_lock'
right after a deploy restart, because the orchestrator's post-restart
run_scan command raced ScannerAgent.setup() (which sets self._scan_lock).
With several deploys in one session, this silently ate more than one scan.

Fix: setup() now runs to completion before the command thread starts.
"""

import threading
import time

import pytest

from agents.base import BaseAgent


class _OrderTrackingAgent(BaseAgent):
    name = "ordertest"
    tick_seconds = 0.01

    def __init__(self):
        super().__init__()
        self.events = []
        self._setup_done = threading.Event()

    def setup(self):
        self.events.append("setup_start")
        time.sleep(0.05)  # widen the race window a slow real setup() would have
        self.events.append("setup_done")
        self._setup_done.set()

    def on_command(self, payload):
        self.events.append("on_command")
        # The actual bug: this used to be able to fire before setup_done.
        assert self._setup_done.is_set(), "on_command fired before setup() completed"
        self.stop()

    def tick(self):
        pass  # idle; only on_command() stops the agent in this test


@pytest.fixture()
def agent(monkeypatch):
    a = _OrderTrackingAgent()

    def fake_subscribe_forever(channel_names, handler, stop_event, poll_seconds=1.0):
        # Mirrors the real contract (blocks until stop_event) without a
        # real Redis connection: deliver exactly one command immediately.
        handler("cmd:ordertest", {"cmd": "ping"})
        stop_event.wait(5.0)

    monkeypatch.setattr(a.bus, "subscribe_forever", fake_subscribe_forever)
    monkeypatch.setattr(a.bus, "heartbeat", lambda *a_, **k_: True)
    return a


def test_setup_completes_before_any_command_is_dispatched(agent):
    t = threading.Thread(target=agent.run, daemon=True)
    t.start()
    t.join(timeout=3.0)

    assert not t.is_alive(), "agent.run() did not stop -- on_command likely never fired"
    assert agent.events == ["setup_start", "setup_done", "on_command"]
