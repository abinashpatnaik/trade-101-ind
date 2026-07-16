"""
agents.base
===========
BaseAgent: shared lifecycle for every agent process.

Provides:
- per-agent rotating log file (``logs/{name}_{MARKET}.log``) + console
- SIGTERM/SIGINT -> graceful stop
- background heartbeat thread (``t101:{M}:hb:{name}`` with TTL)
- optional command-channel subscription (``t101:{M}:cmd:{name}``)
- ``run()`` template: setup() then loop() every ``tick_seconds`` until stopped

Subclasses implement ``setup()`` (cold-read state keys, init resources),
``tick()`` (one iteration of work) and optionally ``on_command(payload)``.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

from config import config, ACTIVE_MARKET
from agents.bus import Bus

_IN_DOCKER = os.path.exists("/app")


def make_logger(name: str) -> logging.Logger:
    """Rotating file + console logger, mirroring the old agent.py setup."""
    log_dir = "/app/logs" if _IN_DOCKER else "trading_agent"
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{name}_{ACTIVE_MARKET}.log")

    logger = logging.getLogger(f"agents.{name}")
    if logger.handlers:  # already configured (e.g. tests importing twice)
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=3)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False
    return logger


class BaseAgent:
    """Template lifecycle for one agent process."""

    #: agent name — used for logs, heartbeat key and command channel
    name: str = "base"
    #: seconds between tick() calls
    tick_seconds: float = 20.0
    #: set False for agents that take no commands
    subscribe_commands: bool = True

    def __init__(self) -> None:
        self.market = ACTIVE_MARKET
        self.config = config
        self.logger = make_logger(self.name)
        self.bus = Bus(self.market, config.bus.redis_url)
        self._stop = threading.Event()
        self._hb_thread: Optional[threading.Thread] = None
        self._cmd_thread: Optional[threading.Thread] = None
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

    # ------------------------------------------------------------------
    # Lifecycle hooks for subclasses
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """One-time init after threads start; cold-read state keys here."""

    def tick(self) -> None:
        """One iteration of the agent's main work."""

    def teardown(self) -> None:
        """Cleanup on shutdown."""

    def on_command(self, payload: Dict[str, Any]) -> None:
        """Handle a message from t101:{M}:cmd:{name}."""

    # ------------------------------------------------------------------
    # Heartbeat / command plumbing
    # ------------------------------------------------------------------

    def _heartbeat_loop(self) -> None:
        period = self.config.bus.heartbeat_period_seconds
        ttl = self.config.bus.heartbeat_ttl_seconds
        while not self._stop.is_set():
            self.bus.heartbeat(self.name, ttl=ttl)
            self._stop.wait(period)

    def _command_loop(self) -> None:
        def handler(channel: str, payload: Dict[str, Any]) -> None:
            self.logger.info("Command received: %s", payload.get("cmd", payload))
            try:
                self.on_command(payload)
                if payload.get("id"):
                    self.bus.publish(
                        "ev:ack",
                        {"id": payload["id"], "agent": self.name, "ok": True},
                    )
            except Exception as exc:
                self.logger.error("Command failed: %s", exc, exc_info=True)
                if payload.get("id"):
                    self.bus.publish(
                        "ev:ack",
                        {"id": payload["id"], "agent": self.name, "ok": False, "detail": str(exc)},
                    )

        self.bus.subscribe_forever([f"cmd:{self.name}"], handler, self._stop)

    def _on_signal(self, signum: int, _frame: Any) -> None:
        self.logger.warning("Received signal %s — shutting down gracefully…", signum)
        self.stop()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def wait(self, seconds: float) -> None:
        """Shutdown-responsive sleep."""
        self._stop.wait(seconds)

    # ------------------------------------------------------------------
    # Main entrypoint
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.logger.info("%s agent starting (market=%s)…", self.name, self.market)
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._hb_thread.start()
        if self.subscribe_commands:
            self._cmd_thread = threading.Thread(target=self._command_loop, daemon=True)
            self._cmd_thread.start()
        try:
            self.setup()
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as exc:
                    # One bad tick must never kill the agent.
                    self.logger.error("tick() error: %s", exc, exc_info=True)
                self._stop.wait(self.tick_seconds)
        finally:
            try:
                self.teardown()
            except Exception as exc:
                self.logger.error("teardown() error: %s", exc, exc_info=True)
            self.logger.info("%s agent stopped.", self.name)
