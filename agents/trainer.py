"""
agents.trainer
==============
TRAINER agent: owns all XGBoost model training for one market.

- ``train_daily``: full retrain of both day+swing models via
  ``ml_trainer.train_model()`` (minutes-scale, network-bound). Scheduled by
  the orchestrator at open−90min; also self-detected when the market is
  closed and no training has run today (weekend/orchestrator-down safety).
- ``train_eod``: incremental retrain from logged features via
  ``continuous_learning.retrain_model_if_needed()`` at NEAR_CLOSE.
- On startup: trains immediately if the model pickles are missing
  (replaces the old trader bootstrap).
- All model writes are atomic (tmp + os.replace, done in ml_trainer /
  continuous_learning), so the trader never loads a torn file. After a
  successful run the trainer SETs ``state:model`` and PUBLISHes ``ev:model``
  which makes the trader hot-reload.

Honors the legacy ``data/last_ml_training_{MARKET}.txt`` tracker so a
mid-migration rollback keeps its once-per-day guarantee.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Any, Dict

from agents.base import BaseAgent

_IN_DOCKER = os.path.exists("/app")
_DATA_DIR = "/app/data" if _IN_DOCKER else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)


class TrainerAgent(BaseAgent):
    name = "trainer"
    tick_seconds = 60.0

    def setup(self) -> None:
        from market_session import MarketSession
        from continuous_learning import ContinuousLearning

        self.session = MarketSession()
        self.continuous_learning = ContinuousLearning()
        self._train_lock = threading.Lock()
        self._tracker_file = os.path.join(_DATA_DIR, f"last_ml_training_{self.market}.txt")

        if self._models_missing():
            self.logger.info("Model pickles missing — bootstrapping initial training.")
            threading.Thread(target=self._train_daily, daemon=True, name="bootstrap").start()

    # ------------------------------------------------------------------

    def _models_missing(self) -> bool:
        for mode in ("day", "swing"):
            path = os.path.join(_DATA_DIR, f"ml_validator_model_{self.market}_{mode}.pkl")
            if not os.path.exists(path):
                return True
        return False

    def _already_trained_today(self) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        if self.bus.get_marker("trainer_daily") == today:
            return True
        try:
            if os.path.exists(self._tracker_file):
                with open(self._tracker_file, "r") as f:
                    return f.read().strip() == today
        except Exception:
            pass
        return False

    def _publish_model_update(self, modes: list) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        state = self.bus.get_state("model") or {}
        for mode in modes:
            state[mode] = now
        self.bus.set_state("model", state)
        self.bus.publish("ev:model", {"modes": modes, "trained_at": now})

    def _train_daily(self) -> None:
        if not self._train_lock.acquire(blocking=False):
            self.logger.info("Training already in progress — skipping daily request.")
            return
        try:
            self.bus.heartbeat(self.name, status="busy", detail="train_daily")
            self.logger.info("Starting daily model training (day + swing)…")
            from ml_trainer import train_model

            success = train_model()
            if success:
                today = datetime.now().strftime("%Y-%m-%d")
                self.bus.set_marker("trainer_daily", today)
                try:
                    with open(self._tracker_file, "w") as f:
                        f.write(today)
                except Exception as exc:
                    self.logger.warning("Could not write legacy tracker file: %s", exc)
                self._publish_model_update(["day", "swing"])
                self.logger.info("Daily training complete — ev:model published.")
            else:
                self.logger.error("Daily training failed or aborted.")
        except Exception as exc:
            self.logger.error("Daily training error: %s", exc, exc_info=True)
        finally:
            self._train_lock.release()

    def _train_eod(self) -> None:
        if not self._train_lock.acquire(blocking=False):
            self.logger.info("Training already in progress — skipping EOD request.")
            return
        try:
            self.bus.heartbeat(self.name, status="busy", detail="train_eod")
            today = datetime.now().strftime("%Y-%m-%d")
            if self.bus.get_marker("trainer_eod") == today:
                self.logger.info("EOD retrain already ran today — skipping.")
                return
            self.logger.info("Starting EOD incremental retrain…")
            self.continuous_learning.retrain_model_if_needed()
            self.bus.set_marker("trainer_eod", today)
            self._publish_model_update(["continuous"])
            self.logger.info("EOD retrain complete — ev:model published.")
        except Exception as exc:
            self.logger.error("EOD retrain error: %s", exc, exc_info=True)
        finally:
            self._train_lock.release()

    # ------------------------------------------------------------------

    def on_command(self, payload: Dict[str, Any]) -> None:
        cmd = payload.get("cmd")
        if cmd == "train_daily":
            threading.Thread(target=self._train_daily, daemon=True, name="train").start()
        elif cmd == "train_eod":
            threading.Thread(target=self._train_eod, daemon=True, name="train").start()

    def tick(self) -> None:
        # Self-detected daily training: market closed + not yet trained today.
        # Covers weekends and orchestrator downtime.
        if self.session.is_market_open():
            return
        if self._already_trained_today():
            return
        if self._train_lock.locked():
            return
        self.logger.info("Self-detected daily training window (market closed).")
        threading.Thread(target=self._train_daily, daemon=True, name="train").start()


def main() -> None:
    TrainerAgent().run()


if __name__ == "__main__":
    main()
