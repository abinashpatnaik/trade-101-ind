"""
agents.trader
=============
The TRADER agent — live trading hot loop, decomposed from the old agent.py.

Keeps in-process (zero bus round-trips): broker connection + websocket
instant exits, the per-symbol scan pipeline, morning gap check, pre-market
dump protection, EOD close-all, daily-loss halt, EOD report, and every
file/DB/UDP contract the dashboard depends on.

Moved OUT to sibling agents: sector scanning (scanner), ML training
(trainer), ticker fetching (scanner). The trader consumes their outputs
through Redis state keys with file/config fallbacks, so it keeps trading
(and above all keeps managing exits) even with the bus down.

Fail-safe policy: if the bus was reachable and goes stale for longer than
``config.bus.buy_suppress_after_seconds``, new BUYs are suppressed (the
blocklist/vetting data may be stale) while exits continue unaffected. If
the bus was NEVER reachable (standalone/rollback mode), the trader runs
exactly like the old monolith on file fallbacks.
"""

import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import logging.handlers
from datetime import datetime, timezone
from typing import Dict, Optional

from config import config, CUR_SYM

ACTIVE_MARKET = os.getenv("TRADING_MARKET", "IN").upper()
if ACTIVE_MARKET == "US":
    from alpaca_connector import AlpacaConnector as BrokerConnector
else:
    from zerodha_connector import ZerodhaConnector as BrokerConnector

from decision_engine import DecisionEngine, Decision
from market_session import MarketSession
from order_executor import OrderExecutor
from portfolio_tracker import PortfolioTracker
from price_feed import PriceFeed
from report_generator import EODReportGenerator
from report_sender import ReportSender
from learning_engine import LearningEngine
from sentiment_engine import SentimentEngine
from trend_engine import TrendEngine

from ai_validator import AIValidator
from continuous_learning import ContinuousLearning
from db import TradingDB

from agents.bus import Bus

# ---------------------------------------------------------------------------
# Logging setup — identical to the old agent.py: root logger into
# logs/agent_{MARKET}.log so every library module keeps the same log file
# (dashboard reads it).
# ---------------------------------------------------------------------------


def _setup_logging() -> logging.Logger:
    log_dir = os.path.dirname(config.agent.log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(module)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=config.agent.log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    return logging.getLogger(__name__)


logger = _setup_logging()


class TradingAgent:
    """Live trading agent for one market (hot loop)."""

    AGENT_NAME = "trader"

    def __init__(self) -> None:
        logger.info("Initialising TradingAgent (trader agent) …")

        # Subsystem instances
        self.session = MarketSession()
        self.price_feed = PriceFeed()
        self.trend_engine = TrendEngine()
        self.sentiment_engine = SentimentEngine()
        self.decision_engine = DecisionEngine()
        self.portfolio = PortfolioTracker()

        self.learning = LearningEngine()
        self.sentiment_engine.set_learning_engine(self.learning)

        self.report_gen = EODReportGenerator()
        self.report_sender = ReportSender()

        self.broker: Optional[BrokerConnector] = None
        self.executor: Optional[OrderExecutor] = None

        # Agent state flags (explicit — no getattr defaults)
        self._running = False
        self._shutdown_requested = False
        self._eod_processed = False
        self._last_pm_check = 0.0

        # Concurrency guards: the broker websocket thread and the main scan
        # loop both touch portfolio.open_positions / executor._open_orders.
        self._positions_lock = threading.RLock()
        self._exiting: set = set()

        # ML validation + feature logging stay in the trader (features are
        # produced here); training itself is the trainer agent's job.
        self.ai_validator = AIValidator()
        self.continuous_learning = ContinuousLearning()
        self._trading_db = TradingDB()
        self._current_signals: Dict[str, Dict] = {}

        # US regulatory: sub-$25K MARGIN accounts get PDT-flagged at 4 day
        # trades per 5 business days. Cash accounts are exempt. Resolved
        # after broker connect (needs a live account query) — see
        # _maybe_enable_pdt_guard().
        self._pdt_guard = None

        if config.ai.enabled and (
            self.ai_validator.model_day is None or self.ai_validator.model_swing is None
        ):
            logger.warning(
                "XGBoost models not found — running degraded on the classic "
                "path until the trainer agent publishes models (ev:model)."
            )

        # --- Bus wiring ---
        self.bus = Bus(ACTIVE_MARKET, config.bus.redis_url)
        self._reload_model_flag = threading.Event()
        self._bus_stop = threading.Event()
        self._blocklist: Dict[str, Dict] = {}

        # Publish every recorded trade for the vetting agent's live-accuracy
        # monitor. Failures inside the hook are swallowed by the tracker.
        def _publish_trade(trade) -> None:
            self.bus.publish(
                "ev:trade",
                {
                    "symbol": trade.symbol,
                    "action": trade.action,
                    "quantity": trade.quantity,
                    "price": trade.price,
                    "pnl": trade.pnl,
                    "exit_reason": trade.exit_reason,
                    "mode": trade.mode,
                },
            )

        self.portfolio.on_trade = _publish_trade

        signal.signal(signal.SIGINT, self.on_shutdown)
        signal.signal(signal.SIGTERM, self.on_shutdown)

        logger.info(
            "TradingAgent initialised. Universe: %d tickers.",
            len(config.universe.tickers),
        )

    # ------------------------------------------------------------------
    # Bus helper threads
    # ------------------------------------------------------------------

    def _start_bus_threads(self) -> None:
        def heartbeat_loop() -> None:
            period = config.bus.heartbeat_period_seconds
            ttl = config.bus.heartbeat_ttl_seconds
            while not self._bus_stop.is_set():
                status = "ok" if self._running else "idle"
                self.bus.heartbeat(self.AGENT_NAME, status=status, ttl=ttl)
                self._bus_stop.wait(period)

        def event_loop() -> None:
            def handler(channel: str, payload: Dict) -> None:
                if channel == "ev:model":
                    logger.info("Model update event received — scheduling reload.")
                    self._reload_model_flag.set()

            self.bus.subscribe_forever(["ev:model"], handler, self._bus_stop)

        threading.Thread(target=heartbeat_loop, daemon=True, name="hb").start()
        threading.Thread(target=event_loop, daemon=True, name="bus-events").start()

    def _buy_suppressed_by_stale_bus(self) -> bool:
        """True when the bus WAS healthy and has gone stale — vetting/blocklist
        data may be outdated, so we stop opening new positions (exits are
        unaffected). Never-connected (standalone mode) does not suppress."""
        if not self.bus.ever_ok:
            return False
        return self.bus.seconds_since_ok() > config.bus.buy_suppress_after_seconds

    def _refresh_bus_inputs(self) -> None:
        """Per-loop: ping bus, pull blocklist + strategy directive."""
        self.bus.ping()

        self._blocklist = self.bus.hgetall_state("blocklist") or {}

        directive = self.bus.get_state("strategy")
        params = None
        if directive and isinstance(directive.get("params"), dict):
            ts_raw = directive.get("ts")
            fresh = True
            if ts_raw:
                try:
                    ts = datetime.fromisoformat(ts_raw)
                    age_min = (datetime.now(ts.tzinfo or timezone.utc) - ts).total_seconds() / 60
                    fresh = age_min <= config.strategy.directive_stale_minutes
                except (ValueError, TypeError):
                    fresh = True
            if fresh:
                params = directive["params"]
        if params:
            self.decision_engine.apply_directive(params)
        else:
            self.decision_engine.clear_directive()

        if self._reload_model_flag.is_set():
            self._reload_model_flag.clear()
            try:
                self.ai_validator.reload_model()
                logger.info("AI validator models reloaded after trainer update.")
            except Exception as exc:
                logger.error("Model reload failed: %s", exc)

    def _effective_targets(self) -> list:
        """Vetted targets from the bus (today only) → daily_targets file →
        static config universe."""
        vetted = self.bus.get_state("vetted_targets")
        if vetted and vetted.get("session_date") == self.session.get_session_date():
            approved = vetted.get("approved")
            if approved and isinstance(approved, list):
                blocked = vetted.get("blocked") or {}
                if blocked:
                    logger.info(
                        "Vetting: %d approved, %d blocked (%s)",
                        len(approved), len(blocked),
                        ", ".join(f"{s}: {r}" for s, r in list(blocked.items())[:5]),
                    )
                return sorted(approved)

        try:
            data_dir = os.path.dirname(config.agent.trades_csv)
            targets_file = os.path.join(data_dir, f"daily_targets_{ACTIVE_MARKET}.json")
            if os.path.exists(targets_file):
                with open(targets_file, "r") as f:
                    parsed = json.load(f)
                if parsed and isinstance(parsed, list):
                    return sorted(parsed)
        except Exception as exc:
            logger.warning(
                "Failed to load daily_targets_%s.json, falling back to config: %s",
                ACTIVE_MARKET, exc,
            )
        return list(config.universe.tickers)

    # ------------------------------------------------------------------
    # Shutdown handler
    # ------------------------------------------------------------------

    def on_shutdown(self, sig: int, frame) -> None:
        sig_name = "SIGINT" if sig == signal.SIGINT else "SIGTERM"
        logger.warning("Received %s — initiating graceful shutdown …", sig_name)
        self._shutdown_requested = True
        self._running = False

    # ------------------------------------------------------------------
    # Broker connection helpers
    # ------------------------------------------------------------------

    def _connect_broker(self) -> bool:
        """Establish broker connection and initialise the OrderExecutor."""
        try:
            self.broker = BrokerConnector()
            self.broker.connect()
            self.executor = OrderExecutor(self.broker)
            self.price_feed.set_broker(self.broker)

            udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            dashboard_host = os.getenv("DASHBOARD_HOST", "127.0.0.1")

            def fast_exit_check(symbol: str, price: float) -> None:
                # Fire-and-forget UDP datagram for the dashboard live ticker —
                # outside the lock, never blocks.
                try:
                    udp_sock.sendto(
                        json.dumps({"symbol": symbol, "price": price}).encode("utf-8"),
                        (dashboard_host, 4000),
                    )
                except Exception:
                    pass

                if self.executor is None:
                    return

                # Read state + claim the exit under the lock; NEVER call the
                # broker while holding it.
                with self._positions_lock:
                    if symbol not in self.portfolio.open_positions:
                        return
                    if symbol in self._exiting:
                        return
                    position = dict(self.portfolio.open_positions[symbol])
                    exit_trigger = self.executor.check_exit_conditions(symbol, price, position)
                    if exit_trigger not in ("STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP"):
                        return
                    qty = float(position.get("quantity", 0))
                    if qty <= 0:
                        return
                    self._exiting.add(symbol)
                    order = self.executor._open_orders.get(symbol)
                    avg_cost = order.entry_price if order else float(position.get("avg_cost", price))

                try:
                    logger.info("INSTANT WebSocket exit triggered for %s: %s", symbol, exit_trigger)
                    if config.agent.observe_only:
                        logger.info(
                            "[OBSERVE MODE] Would close position for %s instantly (reason: %s), skipping.",
                            symbol, exit_trigger,
                        )
                        return
                    closed = self.executor.close_position(symbol, qty)
                    if not closed:
                        logger.warning(
                            "INSTANT exit for %s (%s) was rejected by the broker — "
                            "position remains open, will retry.",
                            symbol, exit_trigger,
                        )
                        return
                    pnl = (price - avg_cost) * qty
                    # Profit-lock exits intend break-even or better;
                    # tiny negatives are tick-gap slippage, not real losses.
                    if exit_trigger == "TRAILING_STOP" and pnl < 0:
                        pnl = 0.0
                    with self._positions_lock:
                        if self.portfolio.is_simulated:
                            self.portfolio.record_trade(
                                symbol=symbol,
                                action="SELL",
                                quantity=qty,
                                price=price,
                                pnl=pnl,
                                exit_reason=exit_trigger,
                            )
                            self.portfolio.open_positions.pop(symbol, None)
                        else:
                            self.portfolio.set_pending_reason(
                                symbol, exit_trigger,
                                self.executor.pop_fill_price(symbol),
                            )
                    self.decision_engine.register_cooldown(symbol, is_loss=(pnl < 0))
                finally:
                    self._exiting.discard(symbol)

            self.broker.on_price_update_callback = fast_exit_check
            logger.info(
                "%s connection established.",
                "Alpaca" if ACTIVE_MARKET == "US" else "Zerodha",
            )
            return True
        except ConnectionError as exc:
            logger.error("Failed to connect to broker: %s", exc)
            return False
        except Exception as exc:
            logger.error("Unexpected broker connection error: %s", exc, exc_info=True)
            return False

    def _disconnect_broker(self) -> None:
        if self.broker and self.broker.is_connected():
            self.broker.disconnect()
            logger.info("Disconnected from broker.")

    def _maybe_enable_pdt_guard(self) -> None:
        """Enable the PDT guard only for real margin accounts — cash
        accounts are exempt from the rule entirely and gating them blocks
        trading for no regulatory reason."""
        if ACTIVE_MARKET != "US" or self.broker is None:
            return
        is_margin = getattr(self.broker, "is_margin_account", lambda: True)()
        if is_margin:
            from agents.pdt_guard import PDTGuard

            self._pdt_guard = PDTGuard(
                self._trading_db, max_day_trades=config.risk.max_day_trades_per_5d
            )
            logger.info(
                "PDT guard ENABLED — margin account detected (max %d day-trades/5 business days).",
                config.risk.max_day_trades_per_5d,
            )
        else:
            self._pdt_guard = None
            logger.info("PDT guard SKIPPED — cash account (PDT rule doesn't apply).")

    # ------------------------------------------------------------------
    # Per-symbol processing
    # ------------------------------------------------------------------

    def _process_symbol(self, symbol: str, buy_eligible: bool = True) -> None:
        """Full analysis + execution pipeline for a single ticker.

        When *buy_eligible* is False the symbol is managed for EXITS only: BUY
        signals are vetoed so the agent never opens or adds to a position that
        is not in today's vetted targets. Held positions that dropped off the
        approved list are still scanned (buy_eligible=False) so their
        stop-loss / trailing / sell logic keeps running.
        """
        try:
            is_held = symbol in self.portfolio.open_positions
            is_blocked = symbol in self._blocklist

            # Blocked and not held: skip entirely (saves API calls).
            if is_blocked and not is_held:
                logger.info(
                    "Skipping %s — blocked by vetting agent (%s).",
                    symbol, self._blocklist[symbol].get("reason", "n/a"),
                )
                return

            # --- 1. Price data ---
            df_day = self.price_feed.get_ohlcv(symbol, period="5d", interval="5m")
            if df_day is None or df_day.empty:
                logger.warning("No intraday OHLCV data for %s — skipping.", symbol)
                return

            df_swing = self.price_feed.get_ohlcv(symbol, period="3mo", interval="1d")
            if df_swing is None or df_swing.empty:
                logger.warning("No daily OHLCV data for %s — falling back to intraday.", symbol)
                df_swing = df_day

            current_price = self.price_feed.get_current_price(symbol)
            if (current_price is None or current_price <= 0) and self.broker is not None:
                current_price = self.broker.get_current_price(symbol)

            if current_price is None or current_price <= 0:
                logger.warning("Invalid price for %s — skipping.", symbol)
                return

            # --- 2. Trend analysis ---
            trend_signal_day = self.trend_engine.analyse(symbol, df_day)
            trend_signal_swing = self.trend_engine.analyse(symbol, df_swing)

            if trend_signal_day is None or trend_signal_swing is None:
                logger.warning("Trend analysis failed for %s — skipping.", symbol)
                return

            # --- 3. Sentiment ---
            sentiment_score = self.sentiment_engine.get_sentiment(symbol)

            try:
                headlines = self.sentiment_engine.get_news_headlines(symbol, limit=3)
                if headlines:
                    logger.debug("Headlines for %s: %s", symbol, " | ".join(headlines[:3]))
            except Exception:
                pass  # Headlines are informational only.

            # Save signal for dashboard (using Day metrics for UI)
            self._current_signals[symbol] = {
                "symbol": symbol,
                "price": current_price,
                "changePct": 0.0,
                "rsi": trend_signal_day.rsi,
                "trendScore": trend_signal_day.overall_trend,
                "macdSignal": trend_signal_day.macd_signal,
                "emaSignal": trend_signal_day.ema_signal,
                "adx": getattr(trend_signal_day, "adx", 0.0),
                "volumeRatio": getattr(trend_signal_day, "volume_ratio", 1.0),
                "signal": "HOLD",
            }

            # --- 4. Decision ---
            portfolio_state = {
                "portfolio_value": self.portfolio.portfolio_value,
                "available_funds": self.portfolio.cash,
                "open_positions": self.portfolio.open_positions,
            }

            ml_confidence_day = self.ai_validator.get_ml_confidence(
                trend_signal_day, sentiment_score, mode="day"
            )
            ml_confidence_swing = self.ai_validator.get_ml_confidence(
                trend_signal_swing, sentiment_score, mode="swing"
            )

            decision: Decision = self.decision_engine.make_decision(
                symbol=symbol,
                trend_signal=trend_signal_day,
                sentiment_score=sentiment_score,
                current_price=current_price,
                portfolio=portfolio_state,
                ml_confidence_day=ml_confidence_day,
                ml_confidence_swing=ml_confidence_swing,
            )

            # --- 4.5 AI Validation ---
            decision = self.ai_validator.validate_decision(
                symbol=symbol,
                trend_signal_day=trend_signal_day,
                trend_signal_swing=trend_signal_swing,
                sentiment_score=sentiment_score,
                decision=decision,
            )

            # --- 4.6 Vetting blocklist + stale-bus BUY gates ---
            if decision.action == "BUY" and not buy_eligible:
                logger.info(
                    "BUY vetoed for %s — not in today's vetted targets "
                    "(held position, exit-only management).", symbol,
                )
                decision.action = "HOLD"
                decision.reason = "Not in today's approved targets — exit-only"
                decision.quantity = 0
            if decision.action == "BUY" and is_blocked:
                logger.info(
                    "BUY vetoed for %s — on vetting blocklist (%s).",
                    symbol, self._blocklist[symbol].get("reason", "n/a"),
                )
                decision.action = "HOLD"
                decision.reason = f"Vetting blocklist: {self._blocklist[symbol].get('reason', 'n/a')}"
                decision.quantity = 0
            if decision.action == "BUY" and self._buy_suppressed_by_stale_bus():
                logger.warning(
                    "BUY suppressed for %s — bus stale for %.0fs (vetting data may "
                    "be outdated). Exits continue normally.",
                    symbol, self.bus.seconds_since_ok(),
                )
                decision.action = "HOLD"
                decision.reason = "Bus stale — new entries suppressed (fail-safe)"
                decision.quantity = 0
            if (
                decision.action == "BUY"
                and self._pdt_guard is not None
                and not self._pdt_guard.can_open_new_position()
            ):
                decision.action = "HOLD"
                decision.reason = (
                    "PDT guard: day-trade budget exhausted (max "
                    f"{config.risk.max_day_trades_per_5d}/5 business days) — "
                    "entry blocked so protective exits can't get stuck."
                )
                decision.quantity = 0

            # --- 4.7 Continuous Learning Log ---
            self.continuous_learning.log_daily_features(
                symbol=symbol,
                trend_signal=trend_signal_day,
                sentiment_score=sentiment_score,
                predicted_prob=decision.ml_confidence,
            )

            logger.info(
                "Final Decision — %s: action=%s confidence=%.3f score=%.3f | %s",
                symbol,
                decision.action,
                decision.confidence,
                decision.combined_score,
                decision.reason[:120],
            )

            # Update signal for dashboard with final action
            if symbol in self._current_signals:
                self._current_signals[symbol]["signal"] = decision.action
                self._current_signals[symbol]["confidence"] = int(decision.confidence * 100)
                self._current_signals[symbol]["combinedScore"] = round(decision.combined_score, 4)
                is_ai_driver = (
                    config.ai.primary_driver and config.ai.enabled and decision.ml_confidence > 0.0
                )
                if is_ai_driver:
                    already_held = symbol in portfolio_state.get("open_positions", {})
                    self._current_signals[symbol]["buyThreshold"] = (
                        self.decision_engine.get_ml_buy_threshold(symbol, already_held)
                    )
                else:
                    self._current_signals[symbol]["buyThreshold"] = config.signal.buy_threshold
                self._current_signals[symbol]["sellThreshold"] = (
                    0.40 if is_ai_driver else config.signal.sell_threshold
                )
                self._current_signals[symbol]["mlConfidence"] = decision.ml_confidence
                self._current_signals[symbol]["mlConfidenceSwing"] = decision.ml_confidence_swing
                if decision.action == "HOLD" and decision.combined_score >= self._current_signals[symbol]["buyThreshold"]:
                    self._current_signals[symbol]["holdReason"] = decision.reason
                else:
                    self._current_signals[symbol]["holdReason"] = ""
                if decision.ai_decision:
                    self._current_signals[symbol]["aiDecision"] = decision.ai_decision
                    self._current_signals[symbol]["aiReason"] = decision.ai_reason
                elif not config.ai.enabled:
                    self._current_signals[symbol]["aiDecision"] = "OFF"
                else:
                    self._current_signals[symbol]["aiDecision"] = "IDLE"

            # --- 5. Execute ---
            if decision.action != "HOLD" and self.executor is not None:
                if config.agent.observe_only:
                    logger.info("[OBSERVE MODE] Would execute %s for %s, skipping.", decision.action, symbol)
                else:
                    success = self.executor.execute(decision, symbol, current_price)
                    if success and decision.action in ("BUY", "SELL"):
                        if decision.action == "BUY" and self._pdt_guard is not None:
                            self._pdt_guard.note_buy(symbol)
                        pnl: Optional[float] = None
                        exit_reason: Optional[str] = None

                        if decision.action == "SELL":
                            position = self.portfolio.open_positions.get(symbol, {})
                            avg_cost = float(position.get("avg_cost", current_price))
                            qty = float(position.get("quantity", decision.quantity))
                            pnl = (current_price - avg_cost) * qty
                            exit_reason = "SELL_SIGNAL"

                        with self._positions_lock:
                            if self.portfolio.is_simulated:
                                self.portfolio.record_trade(
                                    symbol=symbol,
                                    action=decision.action,
                                    quantity=decision.quantity,
                                    price=current_price,
                                    pnl=pnl,
                                    exit_reason=exit_reason,
                                )
                                if decision.action == "SELL":
                                    self.portfolio.open_positions.pop(symbol, None)
                            else:
                                self.portfolio.set_pending_reason(symbol, exit_reason or "BUY")

                        if decision.action == "SELL" and pnl is not None:
                            self.decision_engine.register_cooldown(symbol, is_loss=(pnl < 0))

                        # Notify learning engine on SELL
                        if decision.action == "SELL":
                            try:
                                trade_headlines = self.sentiment_engine.get_last_headlines(symbol)
                                cost_basis = decision.quantity * current_price if decision.quantity and current_price else 1.0
                                pnl_pct = (pnl / cost_basis * 100) if pnl is not None and cost_basis > 0 else 0.0
                                self.learning.on_trade_closed(
                                    symbol=symbol,
                                    action="SELL",
                                    pnl=pnl or 0.0,
                                    pnl_pct=pnl_pct,
                                    sentiment_score=sentiment_score,
                                    trend_score=trend_signal_day.overall_trend,
                                    combined_score=decision.combined_score,
                                    headlines=trade_headlines,
                                )
                            except Exception as _le:
                                logger.debug("Learning engine update failed: %s", _le)

            # --- 6. Exit condition check for existing positions ---
            if self.executor is not None:
                with self._positions_lock:
                    exit_trigger = None
                    if symbol in self.portfolio.open_positions and symbol not in self._exiting:
                        position = dict(self.portfolio.open_positions[symbol])
                        exit_trigger = self.executor.check_exit_conditions(
                            symbol, current_price, position
                        )
                        if exit_trigger in ("STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP"):
                            qty = float(position.get("quantity", 0))
                            if qty > 0:
                                self._exiting.add(symbol)
                                order = self.executor._open_orders.get(symbol)
                                avg_cost = order.entry_price if order else float(position.get("avg_cost", current_price))
                            else:
                                exit_trigger = None
                        else:
                            exit_trigger = None

                if exit_trigger is not None:
                    try:
                        logger.info("Software exit triggered for %s: %s", symbol, exit_trigger)
                        if config.agent.observe_only:
                            logger.info(
                                "[OBSERVE MODE] Would close position for %s (reason: %s), skipping.",
                                symbol, exit_trigger,
                            )
                            return
                        closed = self.executor.close_position(symbol, qty)
                        if not closed:
                            logger.warning(
                                "Software exit for %s (%s) was rejected by the broker — "
                                "position remains open, will retry.",
                                symbol, exit_trigger,
                            )
                            return
                        pnl = (current_price - avg_cost) * qty
                        if exit_trigger == "TRAILING_STOP" and pnl < 0:
                            pnl = 0.0
                        with self._positions_lock:
                            if self.portfolio.is_simulated:
                                self.portfolio.record_trade(
                                    symbol=symbol,
                                    action="SELL",
                                    quantity=qty,
                                    price=current_price,
                                    pnl=pnl,
                                    exit_reason=exit_trigger,
                                )
                                self.portfolio.open_positions.pop(symbol, None)
                            else:
                                self.portfolio.set_pending_reason(
                                    symbol, exit_trigger,
                                    self.executor.pop_fill_price(symbol),
                                )
                        self.decision_engine.register_cooldown(symbol, is_loss=(pnl < 0))
                    finally:
                        self._exiting.discard(symbol)

        except Exception as exc:
            # Never let a single-symbol failure crash the full scan loop.
            logger.error("Unhandled exception processing %s: %s", symbol, exc, exc_info=True)

    # ------------------------------------------------------------------
    # EOD report
    # ------------------------------------------------------------------

    def send_eod_report(self) -> None:
        try:
            session_date = self.session.get_session_date()
            summary = self.portfolio.get_summary()
            performance = self.portfolio.get_performance()

            trades = self._trading_db.get_trades(date=session_date)

            report = self.report_gen.generate(
                session_date=session_date,
                portfolio_summary=summary,
                performance=performance,
                trades=trades,
            )

            success = self.report_sender.send(report)
            if success:
                logger.info("EOD report emailed successfully for session %s.", session_date)
            else:
                logger.warning(
                    "EOD report generation succeeded but email delivery failed "
                    "for session %s — check SMTP credentials.",
                    session_date,
                )
        except Exception as exc:
            logger.error("send_eod_report() failed: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Morning gap-check for overnight positions
    # ------------------------------------------------------------------

    def _morning_gap_check(self) -> None:
        if not self.portfolio.open_positions:
            return

        stop_loss_pct = config.risk.stop_loss_pct

        logger.info(
            "Morning gap-check: evaluating %d overnight position(s)…",
            len(self.portfolio.open_positions),
        )

        for symbol, position in list(self.portfolio.open_positions.items()):
            qty = float(position.get("quantity", 0))
            if qty <= 0:
                continue

            avg_cost = float(position.get("avg_cost", 0))
            if avg_cost <= 0:
                continue

            current_price = self.price_feed.get_current_price(symbol) or 0.0
            if current_price <= 0:
                logger.warning("Morning gap-check: no price for %s — skipping.", symbol)
                continue

            gap_pct = (current_price - avg_cost) / avg_cost

            if gap_pct <= -stop_loss_pct:
                logger.warning(
                    "MORNING GAP-DOWN SELL: %s gapped %.2f%% "
                    "(entry=%.4f, open=%.4f, stop=-%.1f%%). Selling.",
                    symbol, gap_pct * 100, avg_cost, current_price,
                    stop_loss_pct * 100,
                )

                if config.agent.observe_only:
                    logger.info("[OBSERVE MODE] Would sell %s on morning gap-down, skipping.", symbol)
                    continue

                if self.executor is not None:
                    try:
                        success = self.executor.close_position(symbol, qty)
                        if success:
                            pnl = (current_price - avg_cost) * qty
                            with self._positions_lock:
                                if self.portfolio.is_simulated:
                                    self.portfolio.record_trade(
                                        symbol=symbol,
                                        action="SELL",
                                        quantity=qty,
                                        price=current_price,
                                        pnl=pnl,
                                        exit_reason="MORNING_GAP_STOP",
                                    )
                                    self.portfolio.open_positions.pop(symbol, None)
                                else:
                                    self.portfolio.set_pending_reason(
                                        symbol, "MORNING_GAP_STOP",
                                        self.executor.pop_fill_price(symbol),
                                    )
                            logger.info(
                                "Morning gap-stop sold %s x %.4f @ %.4f (PnL=%.2f)",
                                symbol, qty, current_price, pnl,
                            )
                        else:
                            logger.warning(
                                "Morning gap-down SELL for %s was rejected by the broker — "
                                "position remains open.",
                                symbol,
                            )
                    except Exception as exc:
                        logger.error(
                            "Morning gap-check failed to sell %s: %s", symbol, exc, exc_info=True
                        )
            else:
                logger.info(
                    "Morning gap-check OK: %s at %.2f%% (entry=%.4f, open=%.4f). "
                    "Keeping — intraday logic will manage.",
                    symbol, gap_pct * 100, avg_cost, current_price,
                )

    # ------------------------------------------------------------------
    # EOD close-all
    # ------------------------------------------------------------------

    def _evaluate_ml_hold(self, symbol: str) -> bool:
        """True if the SWING model predicts holding overnight is favourable."""
        if not config.ai.enabled or not getattr(self, "ai_validator", None) or not self.ai_validator.enabled:
            return False

        try:
            hist_df = self.price_feed.get_daily_ohlcv(symbol, period="3mo")
            if hist_df is None or hist_df.empty:
                return False

            trend_signal = self.trend_engine.analyse(symbol, hist_df)
            if trend_signal is None:
                return False
            sentiment_score = self.sentiment_engine.get_sentiment(symbol)

            ml_confidence = self.ai_validator.get_ml_confidence(
                trend_signal, sentiment_score, mode="swing"
            )

            # Base threshold 0.65, discounted by positive sentiment (max 0.15).
            threshold = 0.65
            if sentiment_score > 0:
                discount = min(0.15, sentiment_score * 0.15)
                threshold -= discount
                logger.info(
                    "Positive sentiment (%.2f) reduced ML hold threshold to %.2f for %s",
                    sentiment_score, threshold, symbol,
                )

            if ml_confidence >= threshold:
                return True
        except Exception as e:
            logger.warning("Failed to evaluate ML hold for %s: %s", symbol, e)

        return False

    def close_all_positions(self, reason: str = "EOD") -> None:
        if not self.portfolio.open_positions:
            logger.info("close_all_positions(): no open positions to close.")
            return

        logger.info(
            "Closing all %d open positions (%s) …",
            len(self.portfolio.open_positions), reason,
        )

        for symbol, position in list(self.portfolio.open_positions.items()):
            qty = float(position.get("quantity", 0))
            if qty <= 0:
                continue

            # --- OVERNIGHT HOLD: ML-swing conviction ONLY ---
            # Losers are cut same-day: holding a losing day-trade into
            # delivery adds the DP charge (~0.8% on small positions) and
            # overnight gap risk to a trade that is already negative. Only
            # positions the SWING model actively likes stay overnight.
            if reason == "EOD" and self._evaluate_ml_hold(symbol):
                logger.info("ML model predicts overnight swing. Holding %s overnight.", symbol)
                continue

            try:
                current_price = self.price_feed.get_current_price(symbol) or 0.0
                if config.agent.observe_only:
                    logger.info(
                        "[OBSERVE MODE] Would close %s for EOD/shutdown (reason=%s), skipping.",
                        symbol, reason,
                    )
                    continue

                if self.executor is not None:
                    success = self.executor.close_position(symbol, qty)
                else:
                    logger.warning("OrderExecutor not available — cannot close %s.", symbol)
                    continue

                if success:
                    avg_cost = float(position.get("avg_cost", current_price))
                    pnl = (current_price - avg_cost) * qty if current_price > 0 else None
                    with self._positions_lock:
                        if self.portfolio.is_simulated:
                            self.portfolio.record_trade(
                                symbol=symbol,
                                action="SELL",
                                quantity=qty,
                                price=current_price,
                                pnl=pnl,
                                exit_reason=reason,
                            )
                            self.portfolio.open_positions.pop(symbol, None)
                        else:
                            self.portfolio.set_pending_reason(
                                symbol, reason, self.executor.pop_fill_price(symbol),
                            )
                    logger.info("Closed %s x %d @ %.4f (reason=%s)", symbol, qty, current_price, reason)

                    # Notify learning engine for EOD/shutdown closes
                    try:
                        trade_headlines = self.sentiment_engine.get_last_headlines(symbol)
                        cost_basis = qty * current_price if qty > 0 and current_price > 0 else 1.0
                        pnl_pct = ((pnl / cost_basis) * 100) if pnl is not None and cost_basis > 0 else 0.0
                        self.learning.on_trade_closed(
                            symbol=symbol,
                            action="SELL",
                            pnl=pnl or 0.0,
                            pnl_pct=pnl_pct,
                            sentiment_score=0.0,
                            trend_score=0.0,
                            combined_score=0.0,
                            headlines=trade_headlines,
                        )
                    except Exception as _le:
                        logger.debug("Learning engine update (EOD close) failed: %s", _le)
                else:
                    logger.warning(
                        "EOD/shutdown SELL for %s (reason=%s) was rejected by the broker — "
                        "position remains open overnight.",
                        symbol, reason,
                    )

            except Exception as exc:
                logger.error("Error closing position for %s: %s", symbol, exc, exc_info=True)

    # ------------------------------------------------------------------
    # System status for the dashboard
    # ------------------------------------------------------------------

    def _write_system_status(self, is_market_open: bool, agent_status: str, next_open: str = "") -> None:
        try:
            data_dir = os.path.dirname(config.agent.trades_csv)
            if data_dir and not os.path.exists(data_dir):
                os.makedirs(data_dir, exist_ok=True)
            market = os.environ.get("TRADING_MARKET", "IN").upper()
            out_path = os.path.join(data_dir, f"system_status_{market}.json")
            status_data = {
                "market_open": is_market_open,
                "agent_status": agent_status,
                "next_open": next_open,
                "timestamp": time.time(),
            }
            with open(out_path, "w") as f:
                json.dump(status_data, f, indent=2)
        except Exception as e:
            logger.error("Failed to write system status: %s", e)

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("=" * 70)
        logger.info("%s Trader Agent (%s) — starting up", config.market.exchange, ACTIVE_MARKET)
        logger.info("=" * 70)

        self._start_bus_threads()

        # --- Initial portfolio snapshot for dashboard ---
        try:
            if self._connect_broker():
                self.portfolio.update(self.broker)
                logger.info("Initial portfolio snapshot written for dashboard.")
        except Exception as exc:
            logger.warning("Initial portfolio snapshot failed (non-fatal): %s", exc)

        # --- Step 1: Wait for market open ---
        if not self.session.is_market_open():
            self._write_system_status(False, "sleeping", self.session.next_open_time())
            secs = self.session.seconds_to_open()
            if secs > 0:
                logger.info(
                    "Market is closed. Sleeping %.0f s (%.1f min) until next open …",
                    secs, secs / 60,
                )
                slept = 0.0
                while slept < secs and not self._shutdown_requested:
                    self._write_system_status(False, "sleeping", self.session.next_open_time())

                    # --- PRE-MARKET OVERNIGHT PROTECTION ---
                    if self.session.is_pre_market() and self.portfolio.open_positions:
                        if time.monotonic() - self._last_pm_check > 120:
                            self._last_pm_check = time.monotonic()
                            try:
                                for symbol, position in list(self.portfolio.open_positions.items()):
                                    qty = float(position.get("quantity", 0))
                                    if qty <= 0:
                                        continue

                                    current_price = self.price_feed.get_current_price(symbol) or 0.0
                                    avg_cost = float(position.get("avg_cost", current_price))

                                    if current_price > 0 and avg_cost > 0:
                                        drop_pct = (current_price - avg_cost) / avg_cost

                                        if drop_pct <= -0.02:
                                            sent_score = self.sentiment_engine.get_sentiment(symbol)
                                            if sent_score < 0:
                                                logger.warning(
                                                    "PRE-MARKET DUMP TRIGGERED: %s dropped %.2f%% and "
                                                    "news is negative (%.2f). Selling!",
                                                    symbol, drop_pct * 100, sent_score,
                                                )
                                                if self.executor is not None:
                                                    if self.broker is None or not self.broker.is_connected():
                                                        self._connect_broker()
                                                    success = self.executor.close_position(
                                                        symbol, qty, outsideRth=True
                                                    )
                                                    if success:
                                                        fill_price = self.executor.pop_fill_price(symbol)
                                                        exit_price = fill_price if fill_price else current_price
                                                        pnl = (exit_price - avg_cost) * qty
                                                        with self._positions_lock:
                                                            self.portfolio.record_trade(
                                                                symbol=symbol, action="SELL",
                                                                quantity=qty, price=exit_price,
                                                                pnl=pnl, exit_reason="PRE_MARKET_DUMP",
                                                            )
                                                    else:
                                                        logger.warning(
                                                            "PRE-MARKET DUMP SELL for %s was rejected "
                                                            "by the broker — position remains open.",
                                                            symbol,
                                                        )
                            except Exception as e:
                                logger.error("Pre-market protection loop encountered error: %s", e)

                    chunk = min(30.0, secs - slept)
                    time.sleep(chunk)
                    slept += chunk

        if self._shutdown_requested:
            logger.info("Shutdown requested before market open — exiting.")
            return

        # --- Step 2: Connect to Broker ---
        if not self._connect_broker():
            logger.critical("Cannot establish broker connection — aborting agent run.")
            return
        self._maybe_enable_pdt_guard()

        # --- Step 3: Main scan loop ---
        self._running = True

        self._morning_gap_check()
        loop_interval = config.agent.loop_interval_seconds

        try:
            while self._running and not self._shutdown_requested:
                loop_start = time.monotonic()

                if not self.session.is_market_open():
                    logger.info("Market session has ended — exiting scan loop.")
                    self._write_system_status(False, "sleeping", self.session.next_open_time())
                    break

                self._write_system_status(True, "running")

                # Bus inputs: blocklist, strategy directive, model reloads
                self._refresh_bus_inputs()

                # a. Sync portfolio from broker
                try:
                    with self._positions_lock:
                        self.portfolio.update(self.broker)
                        self.executor.sync_positions(self.portfolio.open_positions, self.broker)

                    if hasattr(self.broker, "subscribe") and self.portfolio.open_positions:
                        self.broker.subscribe(list(self.portfolio.open_positions.keys()))

                except Exception as exc:
                    logger.error("Portfolio update failed: %s", exc, exc_info=True)

                # b. Daily loss limit check
                if self.portfolio.check_daily_loss_limit():
                    logger.warning("Daily loss limit breached — ceasing all trading for today.")
                    self.bus.set_state(
                        "halt",
                        {"halted": True, "reason": "DAILY_LOSS_LIMIT"},
                        ex=int(max(60, self.session.seconds_to_open())),
                    )
                    break

                # c. EOD close check (within the close buffer)
                skip_scanning = False
                if self.session.is_near_close():
                    if not self._eod_processed:
                        logger.info(
                            "Approaching session close (%.1f min remaining) — "
                            "closing all open positions.",
                            self.session.minutes_remaining(),
                        )
                        self.close_all_positions(reason="EOD")
                        self._eod_processed = True
                        logger.info(
                            "EOD processing complete. Agent will continue syncing "
                            "prices until market fully closes."
                        )
                    skip_scanning = True

                # d. Per-symbol scan (vetted targets → file → config universe)
                daily_targets = [] if skip_scanning else self._effective_targets()

                # Always exit-manage open positions, even if they dropped off
                # today's vetted targets (or during the EOD buffer). Buys stay
                # gated to daily_targets; held-but-unapproved names are scanned
                # for exits only (buy_eligible=False).
                target_set = set(daily_targets)
                held_only = [
                    s for s in self.portfolio.open_positions.keys() if s not in target_set
                ]
                scan_set = list(daily_targets) + held_only

                logger.info(
                    "--- Scan loop | %s | %d targets (+%d held-only) | %.1f min remaining ---",
                    self.session.get_session_date(),
                    len(daily_targets),
                    len(held_only),
                    self.session.minutes_remaining(),
                )

                for symbol in scan_set:
                    if self._shutdown_requested:
                        break
                    self._process_symbol(symbol, buy_eligible=(symbol in target_set))

                # Dump signals for dashboard (and prune rows from previous
                # sessions so stale hold-reasons never surface in the UI)
                try:
                    signals_list = list(self._current_signals.values())
                    self._trading_db.upsert_signals(signals_list)
                    pruned = self._trading_db.delete_stale_signals(max_age_hours=24)
                    if pruned:
                        logger.info("Pruned %d stale signal row(s) from previous sessions.", pruned)
                except Exception as exc:
                    logger.error("Failed to dump signals to DB: %s", exc, exc_info=True)

                # e. Portfolio summary log
                summary = self.portfolio.get_summary()
                logger.info(
                    "Portfolio: nav=%s%.2f cash=%s%.2f positions=%d daily_pnl=%s%.2f (%.3f%%)",
                    CUR_SYM, summary["portfolio_value"],
                    CUR_SYM, summary["cash"],
                    summary["open_positions_count"],
                    CUR_SYM, summary["daily_pnl"],
                    summary["daily_loss_pct"],
                )

                # f. Sleep for remainder of the interval
                elapsed = time.monotonic() - loop_start
                sleep_time = max(0.0, loop_interval - elapsed)
                if sleep_time > 0 and not self._shutdown_requested:
                    logger.debug("Sleeping %.1f s until next scan …", sleep_time)
                    slept = 0.0
                    while slept < sleep_time and not self._shutdown_requested:
                        chunk = min(5.0, sleep_time - slept)
                        time.sleep(chunk)
                        slept += chunk

        except Exception as exc:
            logger.critical("Unhandled exception in main loop: %s", exc, exc_info=True)

        finally:
            self._write_system_status(False, "offline")
            if self._shutdown_requested:
                if config.agent.liquidate_on_shutdown:
                    logger.info("Shutdown signal received — liquidating all open positions.")
                    self.close_all_positions(reason="SHUTDOWN")
                else:
                    logger.info(
                        "Shutdown signal received — liquidate_on_shutdown is False, "
                        "keeping positions open."
                    )

            perf = self.portfolio.get_performance()
            logger.info(
                "Session performance: trades=%d win_rate=%.1f%% "
                "total_pnl=%s%.2f best=%s%.2f worst=%s%.2f",
                perf["num_trades"],
                perf["win_rate"],
                CUR_SYM, perf["total_pnl"],
                CUR_SYM, perf["best_trade"],
                CUR_SYM, perf["worst_trade"],
            )

            # Only send the EOD report if the market is closed or near close —
            # avoids spurious emails on mid-session container restarts.
            if not self.session.is_market_open() or self.session.is_near_close():
                self.send_eod_report()
            else:
                logger.info(
                    "Skipping EOD report — market still open (%.1f min remaining). "
                    "This appears to be a mid-session restart.",
                    self.session.minutes_remaining(),
                )
            self._bus_stop.set()
            self._disconnect_broker()
            logger.info("=" * 70)
            logger.info("Trader agent shutdown complete.")
            logger.info("=" * 70)


def main() -> None:
    TradingAgent().run()


if __name__ == "__main__":
    main()
