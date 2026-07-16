"""
agents.vetting
==============
VETTING agent: profit-vets nominated stocks before and while they trade.

Two independent halves:

(a) **Backtest screen** — on ``ev:targets`` (or ``cmd:vetting revet``): replay
    each nominated symbol's recent 5m bars through the live entry logic +
    exit math (``agents.backtest_sim``). Symbols whose replay actually traded
    and lost are blocked from today's target list. Publishes
    ``state:vetted_targets`` + ``ev:vetted`` and writes an additive
    ``data/vetting_report_{MARKET}.json`` for observability.

(b) **Live-accuracy monitor** — on ``ev:trade`` (and a 5-min timer): recompute
    per-symbol hit-rates from the SQLite trades table (durable — the event is
    only a wake-up). Symbols the system has recently been wrong about are
    blocked until the next session open via the ``state:blocklist`` hash.

Data discipline:
- PriceFeed here NEVER gets a broker (yfinance only — protects Zerodha's
  historical-data credit cap).
- This agent never writes the SQLite DB — read-only consumer.
- Data failures degrade to PASS: absence of evidence never blocks trading.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, List

from agents.base import BaseAgent
from agents.backtest_sim import SimParams, replay, verdict

_IN_DOCKER = os.path.exists("/app")
_DATA_DIR = "/app/data" if _IN_DOCKER else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)


class VettingAgent(BaseAgent):
    name = "vetting"
    tick_seconds = 300.0  # accuracy-monitor timer

    def setup(self) -> None:
        from market_session import MarketSession
        from price_feed import PriceFeed
        from trend_engine import TrendEngine
        from decision_engine import DecisionEngine
        from ai_validator import AIValidator
        from db import TradingDB

        self.session = MarketSession()
        self.price_feed = PriceFeed()
        # Guard the Zerodha historical-credit cap: this agent must stay
        # yfinance-only. PriceFeed without set_broker() never hits the broker.
        assert getattr(self.price_feed, "_broker", None) is None, (
            "VettingAgent's PriceFeed must not have a broker attached"
        )
        self.trend_engine = TrendEngine()
        self.decision_engine = DecisionEngine()
        # Score backtest bars with the same ML model the live system uses so the
        # screen tests the live AI-driven entry path (requires AI_VALIDATION_ENABLED
        # + AI_PRIMARY_DRIVER in this container's env; degrades to the classic path
        # if models are unavailable).
        self.ai_validator = AIValidator()
        self.db = TradingDB()  # read-only usage
        self._vet_lock = threading.Lock()

        # Event listener: ev:targets triggers a re-vet, ev:trade wakes the
        # accuracy monitor.
        def handler(channel: str, payload: Dict[str, Any]) -> None:
            if channel == "ev:targets":
                symbols = payload.get("symbols") or []
                source = payload.get("source", "unknown")
                threading.Thread(
                    target=self._vet_targets, args=(symbols, source),
                    daemon=True, name="vet",
                ).start()
            elif channel == "ev:trade" and payload.get("action") == "SELL":
                # React immediately — a fresh loss may push a symbol over the
                # blocking threshold mid-session.
                threading.Thread(
                    target=self._update_accuracy_blocklist, daemon=True, name="acc"
                ).start()

        self._listener = threading.Thread(
            target=lambda: self.bus.subscribe_forever(
                ["ev:targets", "ev:trade"], handler, self._stop
            ),
            daemon=True,
            name="vet-events",
        )
        self._listener.start()
        self.logger.info("Vetting agent ready (backtest screen + accuracy monitor).")

    # ------------------------------------------------------------------
    # (a) Backtest screen
    # ------------------------------------------------------------------

    @staticmethod
    def _median_daily_turnover(df) -> float:
        """Median of per-day traded value (Close × Volume summed per session).
        Returns None when the frame is unusable — absence of evidence never
        blocks."""
        try:
            if df is None or df.empty or "Volume" not in df.columns:
                return None
            per_day = (df["Close"] * df["Volume"]).groupby(df.index.date).sum()
            if per_day.empty:
                return None
            return float(per_day.median())
        except Exception:
            return None

    def _load_nominations(self) -> List[str]:
        targets_file = os.path.join(_DATA_DIR, f"daily_targets_{self.market}.json")
        try:
            with open(targets_file, "r") as f:
                parsed = json.load(f)
            if isinstance(parsed, list):
                return parsed
        except Exception as exc:
            self.logger.warning("Could not read daily targets: %s", exc)
        return []

    def _vet_targets(self, symbols: List[str], source: str) -> None:
        if not symbols:
            symbols = self._load_nominations()
        if not symbols:
            self.logger.info("No nominations to vet (source=%s).", source)
            return
        if not self._vet_lock.acquire(blocking=False):
            self.logger.info("Vetting already in progress — skipping %s request.", source)
            return
        try:
            self.bus.heartbeat(self.name, status="busy", detail=f"vet:{source}")
            cfg = self.config.vetting
            params = SimParams()
            approved: List[str] = []
            blocked: Dict[str, str] = {}
            report: Dict[str, Any] = {}

            self.logger.info(
                "Backtest-vetting %d nominations (source=%s, lookback=%s/%s)…",
                len(symbols), source, cfg.backtest_lookback_period, cfg.backtest_interval,
            )

            for symbol in symbols:
                if self.stopped:
                    break
                try:
                    df = self.price_feed.get_ohlcv(
                        symbol,
                        period=cfg.backtest_lookback_period,
                        interval=cfg.backtest_interval,
                    )

                    # --- Liquidity screen (before the backtest) ---
                    # Illiquid names carry spreads the slippage model can't
                    # see; median daily traded value must clear the floor.
                    turnover = self._median_daily_turnover(df)
                    if turnover is not None and turnover < cfg.min_daily_turnover:
                        reason = (
                            f"illiquid: median daily turnover "
                            f"{turnover:,.0f} < {cfg.min_daily_turnover:,.0f}"
                        )
                        blocked[symbol] = reason
                        report[symbol] = {"verdict": "FAIL", "reason": reason}
                        self.logger.info("BLOCKED %s — %s", symbol, reason)
                        continue

                    result = replay(symbol, df, self.decision_engine, self.trend_engine, params, self.ai_validator)
                except Exception as exc:
                    self.logger.warning("Vet replay failed for %s (PASS by default): %s", symbol, exc)
                    approved.append(symbol)
                    report[symbol] = {"verdict": "PASS", "error": str(exc)}
                    continue

                v = verdict(result, cfg.ev_threshold_pct, getattr(cfg, "min_backtest_trades", 0))
                report[symbol] = {
                    "verdict": v,
                    "n_trades": result.n_trades,
                    "wins": result.wins,
                    "total_return_pct": round(result.total_return_pct, 3),
                    "error": result.error,
                }
                if v == "FAIL":
                    reason = (
                        f"backtest_ev={result.total_return_pct:.2f}% "
                        f"over {result.n_trades} trades"
                    )
                    blocked[symbol] = reason
                    self.logger.info("BLOCKED %s — %s", symbol, reason)
                else:
                    approved.append(symbol)

            session_date = self.session.get_session_date()
            self.bus.set_state(
                "vetted_targets",
                {
                    "session_date": session_date,
                    "approved": sorted(approved),
                    "blocked": blocked,
                    "source": source,
                },
            )
            self.bus.publish("ev:vetted", {"approved": sorted(approved), "blocked": blocked})
            if source == "premarket":
                self.bus.set_marker("vetting_premarket", session_date)

            # Additive observability file — not consumed by the dashboard yet.
            try:
                report_path = os.path.join(_DATA_DIR, f"vetting_report_{self.market}.json")
                with open(report_path + ".tmp", "w") as f:
                    json.dump(
                        {
                            "session_date": session_date,
                            "source": source,
                            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "results": report,
                        },
                        f,
                        indent=2,
                    )
                os.replace(report_path + ".tmp", report_path)
            except Exception as exc:
                self.logger.warning("Could not write vetting report: %s", exc)

            self.logger.info(
                "Vetting complete (source=%s): %d approved, %d blocked.",
                source, len(approved), len(blocked),
            )
        finally:
            self._vet_lock.release()

    # ------------------------------------------------------------------
    # (b) Live-accuracy blocklist
    # ------------------------------------------------------------------

    def _update_accuracy_blocklist(self) -> None:
        cfg = self.config.vetting
        try:
            trades = self.db.get_trades(limit=2000)
        except Exception as exc:
            self.logger.warning("Accuracy monitor: DB read failed: %s", exc)
            return

        sells = [t for t in trades if str(t.get("action", "")).upper() == "SELL"]
        if not sells:
            return

        # Restrict to the most recent N sessions
        session_dates = sorted({t["date"] for t in sells}, reverse=True)
        recent_dates = set(session_dates[: cfg.accuracy_lookback_sessions])
        sells = [t for t in sells if t["date"] in recent_dates]

        today = self.session.get_session_date()
        next_open = self.session.next_open_time()

        by_symbol: Dict[str, List[Dict]] = {}
        for t in sells:
            by_symbol.setdefault(t["symbol"], []).append(t)

        current = self.bus.hgetall_state("blocklist")
        added: Dict[str, Dict] = {}
        removed: List[str] = []

        for symbol, symbol_sells in by_symbol.items():
            # get_trades returns newest-first; keep the rolling window
            window = symbol_sells[: cfg.accuracy_window_trades]
            n = len(window)
            wins = sum(1 for t in window if (t.get("pnl") or 0) > 0)
            hit_rate = wins / n if n else 1.0

            todays = [t for t in window if t["date"] == today]
            consecutive_stops = 0
            for t in todays:  # newest-first
                if t.get("exit_reason") == "STOP_LOSS":
                    consecutive_stops += 1
                else:
                    break

            reason = None
            if n >= cfg.min_trades_to_judge and hit_rate < cfg.min_hit_rate:
                reason = f"hit_rate {hit_rate:.0%} over last {n} sells"
            elif consecutive_stops >= cfg.consecutive_stop_losses_to_block:
                reason = f"{consecutive_stops} consecutive same-session stop-losses"

            if reason:
                entry = {
                    "reason": reason,
                    "hit_rate": round(hit_rate, 3),
                    "n": n,
                    "until": next_open,
                }
                prev = current.get(symbol)
                if not prev or prev.get("reason") != reason:
                    added[symbol] = entry
                self.bus.hset_state("blocklist", symbol, entry)

        # Prune expired entries (block until next session open)
        now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
        for symbol, entry in current.items():
            until = entry.get("until", "")
            if until and until <= now_iso and symbol not in added:
                self.bus.hdel_state("blocklist", symbol)
                removed.append(symbol)

        if added or removed:
            self.bus.publish("ev:blocklist", {"added": added, "removed": removed})
            self.logger.info(
                "Blocklist updated: +%d (%s) -%d (%s)",
                len(added), ", ".join(added) or "-",
                len(removed), ", ".join(removed) or "-",
            )

    # ------------------------------------------------------------------

    def on_command(self, payload: Dict[str, Any]) -> None:
        if payload.get("cmd") == "revet":
            source = (payload.get("args") or {}).get("source", "command")
            threading.Thread(
                target=self._vet_targets, args=([], source), daemon=True, name="vet"
            ).start()

    def tick(self) -> None:
        # Periodic recompute (SELL events also trigger one immediately).
        self._update_accuracy_blocklist()

        # Pre-market safety net: if the scanner nominated but nobody vetted
        # yet today (orchestrator down), self-trigger.
        if self.session.is_pre_market():
            today = self.session.get_session_date()
            if (
                self.bus.get_marker("scanner_premarket") == today
                and self.bus.get_marker("vetting_premarket") != today
                and not self._vet_lock.locked()
            ):
                self.logger.info("Self-detected unvetted pre-market nominations.")
                threading.Thread(
                    target=self._vet_targets, args=([], "premarket"),
                    daemon=True, name="vet",
                ).start()


def main() -> None:
    VettingAgent().run()


if __name__ == "__main__":
    main()
