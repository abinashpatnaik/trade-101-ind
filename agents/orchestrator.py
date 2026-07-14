"""
agents.orchestrator
===================
The ORCHESTRATOR — primary agent in charge of one market's agent fleet.

Three responsibilities, all on a 20s tick:

1. **Session clock** — publishes ``state:session``
   (PRE_MARKET | OPEN | NEAR_CLOSE | CLOSED) every tick and ``ev:session``
   on transitions, using the same MarketSession the trader gates on.

2. **Job scheduler** (idempotent via ``last_run:sched_*`` markers):
   - open−90min          → ``cmd:trainer  train_daily``
   - pre-market start    → ``cmd:scanner  run_scan {source: premarket}``
   - on ``ev:targets``   → ``cmd:vetting  revet`` (belt-and-braces; vetting
                            also listens to ev:targets directly)
   - open−10min + every 15min while OPEN → ``cmd:strategy classify``
   - every 60min while OPEN → ``cmd:scanner run_scan {source: intraday}``
   - NEAR_CLOSE transition  → ``cmd:trainer train_eod``

3. **Health supervisor** — watches every sub-agent's heartbeat TTL key.
   An agent whose heartbeat has been missing for >3 periods while its
   container reports "running" is restarted through the Docker socket
   (same privilege the dashboards already hold). Restarts are budgeted
   (3/agent/day) to prevent crash-loop amplification, and trader restarts
   are suppressed for 5 minutes after session close (the trader exits by
   design post-session; docker's restart policy revives it).

The orchestrator heals and schedules — it is deliberately NOT a trade gate:
traders self-gate on their own MarketSession, so trading never depends on
this process being alive.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Dict, Optional

from agents.base import BaseAgent

#: Sub-agents to supervise: heartbeat name -> env var with container name
SUPERVISED = {
    "trader": "CONTAINER_TRADER",
    "scanner": "CONTAINER_SCANNER",
    "vetting": "CONTAINER_VETTING",
    "strategy": "CONTAINER_STRATEGY",
    "trainer": "CONTAINER_TRAINER",
}


class OrchestratorAgent(BaseAgent):
    name = "orchestrator"
    subscribe_commands = True

    def __init__(self) -> None:
        super().__init__()
        self.tick_seconds = float(self.config.orchestrator.tick_seconds)

    def setup(self) -> None:
        from market_session import MarketSession

        self.session = MarketSession()
        self._prev_state: Optional[str] = None
        self._closed_since: float = 0.0
        self._last_strategy_cmd: float = 0.0
        self._last_intraday_cmd: float = 0.0
        self._hb_missing_since: Dict[str, float] = {}
        self._restart_counts: Dict[str, int] = {}
        self._restart_count_date: str = ""

        self._docker = None
        try:
            import docker

            self._docker = docker.from_env()
            self.logger.info("Docker supervision enabled.")
        except Exception as exc:
            self.logger.warning(
                "Docker SDK unavailable (%s) — health supervision is observe-only.", exc
            )

        # Relay: nominations always get vetted, even if vetting missed the event.
        def handler(channel: str, payload: Dict[str, Any]) -> None:
            if channel == "ev:targets":
                source = payload.get("source", "unknown")
                self._send_cmd("vetting", "revet", {"source": source})

        threading.Thread(
            target=lambda: self.bus.subscribe_forever(["ev:targets"], handler, self._stop),
            daemon=True,
            name="orch-events",
        ).start()

        self.logger.info("Orchestrator ready (market=%s).", self.market)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_cmd(self, agent: str, cmd: str, args: Optional[Dict] = None) -> None:
        self.logger.info("Scheduling: cmd:%s %s %s", agent, cmd, args or {})
        self.bus.publish(
            f"cmd:{agent}",
            {"cmd": cmd, "args": args or {}, "id": str(uuid.uuid4())},
        )

    def _session_state(self) -> str:
        if self.session.is_market_open():
            return "NEAR_CLOSE" if self.session.is_near_close() else "OPEN"
        if self.session.is_pre_market():
            return "PRE_MARKET"
        return "CLOSED"

    def _sched_marker_is_today(self, job: str) -> bool:
        return self.bus.get_marker(f"sched_{job}") == self.session.get_session_date()

    def _mark_sched(self, job: str) -> None:
        self.bus.set_marker(f"sched_{job}", self.session.get_session_date())

    # ------------------------------------------------------------------
    # 1. Session clock
    # ------------------------------------------------------------------

    def _publish_session(self, state: str) -> None:
        payload = {
            "state": state,
            "session_date": self.session.get_session_date(),
            "seconds_to_open": round(self.session.seconds_to_open(), 1),
            "minutes_remaining": round(self.session.minutes_remaining(), 1),
            "next_open": self.session.next_open_time(),
        }
        self.bus.set_state("session", payload)
        if state != self._prev_state:
            self.bus.publish("ev:session", payload)
            self.logger.info("Session transition: %s -> %s", self._prev_state, state)
            if state == "CLOSED":
                self._closed_since = time.monotonic()
            if state == "NEAR_CLOSE" and not self._sched_marker_is_today("train_eod"):
                self._mark_sched("train_eod")
                self._send_cmd("trainer", "train_eod")
            self._prev_state = state

    # ------------------------------------------------------------------
    # 2. Scheduler
    # ------------------------------------------------------------------

    def _schedule_jobs(self, state: str) -> None:
        cfg = self.config.orchestrator
        secs_to_open = self.session.seconds_to_open()

        # Daily training at open−90min (works from CLOSED or PRE_MARKET)
        if (
            state in ("CLOSED", "PRE_MARKET")
            and 0 < secs_to_open <= cfg.train_daily_minutes_before_open * 60
            and not self._sched_marker_is_today("train_daily")
        ):
            self._mark_sched("train_daily")
            self._send_cmd("trainer", "train_daily")

        # Pre-market sector scan (once per day)
        if state == "PRE_MARKET" and not self._sched_marker_is_today("scanner_premarket"):
            self._mark_sched("scanner_premarket")
            self._send_cmd("scanner", "run_scan", {"source": "premarket"})

        # Strategy classification: once at open−10min, then every 15min while OPEN
        if (
            state in ("CLOSED", "PRE_MARKET")
            and 0 < secs_to_open <= cfg.strategy_minutes_before_open * 60
            and not self._sched_marker_is_today("strategy_preopen")
        ):
            self._mark_sched("strategy_preopen")
            self._send_cmd("strategy", "classify")

        if state == "OPEN":
            now = time.monotonic()
            if now - self._last_strategy_cmd >= self.config.strategy.classify_interval_minutes * 60:
                self._last_strategy_cmd = now
                self._send_cmd("strategy", "classify")
            if now - self._last_intraday_cmd >= cfg.intraday_scan_interval_minutes * 60:
                self._last_intraday_cmd = now
                self._send_cmd("scanner", "run_scan", {"source": "intraday"})

    # ------------------------------------------------------------------
    # 3. Health supervisor
    # ------------------------------------------------------------------

    def _reset_restart_budget_if_new_day(self) -> None:
        today = self.session.get_session_date()
        if today != self._restart_count_date:
            self._restart_count_date = today
            self._restart_counts = {}

    def _supervise(self) -> None:
        self._reset_restart_budget_if_new_day()
        cfg_bus = self.config.bus
        cfg = self.config.orchestrator
        missing_grace = 3 * cfg_bus.heartbeat_period_seconds
        now = time.monotonic()

        for agent, env_var in SUPERVISED.items():
            hb = self.bus.get_heartbeat(agent)
            if hb is not None:
                self._hb_missing_since.pop(agent, None)
                continue

            first_missing = self._hb_missing_since.setdefault(agent, now)
            if now - first_missing < missing_grace:
                continue

            # Trader exits by design after session close — give docker's
            # restart policy time before intervening.
            if (
                agent == "trader"
                and self._closed_since
                and now - self._closed_since < cfg.trader_restart_suppress_seconds
            ):
                continue

            container_name = os.getenv(env_var, "")
            if not container_name or self._docker is None:
                self.logger.warning(
                    "Heartbeat missing for %s (>%ds) — no docker supervision available.",
                    agent, int(now - first_missing),
                )
                continue

            if self._restart_counts.get(agent, 0) >= cfg.max_restarts_per_agent_per_day:
                self.logger.critical(
                    "Heartbeat missing for %s but restart budget (%d/day) exhausted — "
                    "manual intervention required.",
                    agent, cfg.max_restarts_per_agent_per_day,
                )
                continue

            try:
                container = self._docker.containers.get(container_name)
                if container.status != "running":
                    # Not running: docker's restart policy owns this case.
                    continue
                self.logger.warning(
                    "Restarting %s (container=%s): heartbeat missing for %ds.",
                    agent, container_name, int(now - first_missing),
                )
                container.restart(timeout=30)
                self._restart_counts[agent] = self._restart_counts.get(agent, 0) + 1
                self._hb_missing_since.pop(agent, None)
            except Exception as exc:
                self.logger.error("Failed to restart %s: %s", container_name, exc)

    # ------------------------------------------------------------------

    def on_command(self, payload: Dict[str, Any]) -> None:
        cmd = payload.get("cmd")
        if cmd == "halt":
            self.bus.set_state("halt", {"halted": True, "reason": "MANUAL"})
            self.logger.warning("Manual HALT set.")
        elif cmd == "resume":
            self.bus.delete_state("halt")
            self.logger.warning("Manual halt cleared.")

    def tick(self) -> None:
        state = self._session_state()
        self._publish_session(state)
        self._schedule_jobs(state)
        self._supervise()


def main() -> None:
    OrchestratorAgent().run()


if __name__ == "__main__":
    main()
