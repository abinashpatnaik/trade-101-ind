"""
agents.scanner
==============
SCANNER agent: nominates daily trading targets and feeds the dashboard ticker.

- Hosts the TickerFetcher background thread permanently, so the dashboard's
  ``data/ticker_{MARKET}.json`` keeps updating even while the trader restarts.
- Runs ``sector_scanner.run_scanner()`` once per day in the pre-market window
  (self-detected, idempotent via the ``last_run:scanner_premarket`` marker)
  and on demand via ``cmd:scanner {"cmd": "run_scan"}`` from the orchestrator
  (which also schedules the hourly intraday re-scans).
- Output contract unchanged: ``data/daily_targets_{MARKET}.json``; publishes
  ``ev:targets`` so the vetting agent can re-vet immediately.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict

from agents.base import BaseAgent

_IN_DOCKER = os.path.exists("/app")
_DATA_DIR = "/app/data" if _IN_DOCKER else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)


class ScannerAgent(BaseAgent):
    name = "scanner"
    tick_seconds = 30.0

    def setup(self) -> None:
        from ticker_fetcher import TickerFetcher
        from market_session import MarketSession

        self.session = MarketSession()
        self.ticker_fetcher = TickerFetcher()
        self.ticker_fetcher.start()
        self._scan_lock = threading.Lock()
        self.logger.info("Ticker fetcher started; scanner ready.")

    def teardown(self) -> None:
        try:
            self.ticker_fetcher.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------

    def _run_scan(self, source: str) -> None:
        """Run the sector scanner and publish nominations. Serialised —
        overlapping scan requests are dropped, not queued."""
        if not self._scan_lock.acquire(blocking=False):
            self.logger.info("Scan already in progress — skipping %s request.", source)
            return
        try:
            self.bus.heartbeat(self.name, status="busy", detail=f"scan:{source}")
            self.logger.info("Running sector scanner (source=%s)…", source)
            import sector_scanner

            sector_scanner.run_scanner()

            symbols = []
            targets_file = os.path.join(_DATA_DIR, f"daily_targets_{self.market}.json")
            try:
                with open(targets_file, "r") as f:
                    parsed = json.load(f)
                if isinstance(parsed, list):
                    symbols = parsed
            except Exception as exc:
                self.logger.warning("Could not read %s after scan: %s", targets_file, exc)

            self.bus.publish("ev:targets", {"symbols": symbols, "source": source})
            self.logger.info(
                "Sector scan complete (source=%s): %d targets published.",
                source, len(symbols),
            )
        except Exception as exc:
            self.logger.error("Sector scan failed (source=%s): %s", source, exc, exc_info=True)
        finally:
            self._scan_lock.release()

    # ------------------------------------------------------------------

    def on_command(self, payload: Dict[str, Any]) -> None:
        if payload.get("cmd") == "run_scan":
            source = (payload.get("args") or {}).get("source", "intraday")
            if source == "premarket":
                self.bus.set_marker("scanner_premarket", self.session.get_session_date())
            threading.Thread(
                target=self._run_scan, args=(source,), daemon=True, name="scan"
            ).start()

    def tick(self) -> None:
        # Self-detected pre-market scan — belt and braces in case the
        # orchestrator is down. Idempotent via the last_run marker.
        if not self.session.is_pre_market():
            return
        today = self.session.get_session_date()
        if self.bus.get_marker("scanner_premarket") == today:
            return
        self.bus.set_marker("scanner_premarket", today)
        self._run_scan("premarket")


def main() -> None:
    ScannerAgent().run()


if __name__ == "__main__":
    main()
