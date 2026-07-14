"""
agents.healthcheck
==================
Docker HEALTHCHECK helper.

Usage (in Dockerfile / compose):
    HEALTHCHECK CMD python -m agents.healthcheck

Reads HC_AGENT (agent name) and TRADING_MARKET from the environment and
exits 0 iff that agent's heartbeat key is present (fresh) in Redis.
Informational only — the orchestrator is the actor that restarts agents.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    agent = os.getenv("HC_AGENT", "")
    if not agent:
        # No agent configured — treat as healthy (container-level default).
        return 0
    from agents.bus import Bus

    bus = Bus(os.getenv("TRADING_MARKET", "IN"))
    hb = bus.get_heartbeat(agent)
    return 0 if hb is not None else 1


if __name__ == "__main__":
    sys.exit(main())
