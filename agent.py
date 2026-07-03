"""
agent.py
========
Main orchestrator for the automated multi-market trading agent.

Architecture
------------
TradingAgent wires together all subsystems and drives the main event loop:

    MarketSession ──► (gating)
    PriceFeed ────────────────► TrendEngine ─────────► DecisionEngine ──► OrderExecutor
    SentimentEngine ──────────►                  │
                                                  └── PortfolioTracker (state context)

Running
-------
    python agent.py

The agent will:
  1. Wait until LSE opens (08:00 London time) if run outside market hours.
  2. Connect to IBKR TWS / IB Gateway.
  3. Scan all tickers every 60 seconds.
  4. At 16:15 (15 min before close) close all open positions.
  5. Disconnect gracefully and log the session summary.

Signals SIGINT (Ctrl-C) and SIGTERM both trigger a graceful shutdown that
closes all open positions before exiting.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
import logging.handlers
from typing import Optional

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

# ML Validator & Scanner additions
from ai_validator import AIValidator
from continuous_learning import ContinuousLearning
from ticker_fetcher import TickerFetcher
from db import TradingDB

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    """
    Configure root-level logging with:
      - A StreamHandler (console) at INFO level.
      - A RotatingFileHandler writing to agent.log at DEBUG level.
    """
    # Ensure log directory exists.
    log_dir = os.path.dirname(config.agent.log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(module)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler (INFO+)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    # Rotating file handler (DEBUG+, 10 MB max, keep 5 backups)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=config.agent.log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    return logging.getLogger(__name__)


logger = _setup_logging()


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------

class TradingAgent:
    """
    Top-level orchestrator for the automated multi-market trading agent.

    Lifecycle
    ---------
    1. __init__: initialise all subsystem objects.
    2. run():    main event loop (blocks until market close or shutdown signal).
    3. close_all_positions(): EOD or emergency liquidation.
    4. on_shutdown(): SIGINT/SIGTERM handler.

    All subsystems are initialised in __init__; connection to IBKR is
    established in run() after the market-open check to avoid holding a
    long-lived idle connection.
    """

    def __init__(self) -> None:
        logger.info("Initialising TradingAgent …")

        # Subsystem instances
        self.session = MarketSession()
        self.price_feed = PriceFeed()
        self.trend_engine = TrendEngine()
        self.sentiment_engine = SentimentEngine()
        self.decision_engine = DecisionEngine()
        self.portfolio = PortfolioTracker()

        # Learning engine — wire up to sentiment engine
        self.learning = LearningEngine()
        self.sentiment_engine.set_learning_engine(self.learning)

        # EOD report subsystems
        self.report_gen = EODReportGenerator()
        self.report_sender = ReportSender()

        # Broker connector and order executor (connected in run())
        self.broker: Optional[BrokerConnector] = None
        self.executor: Optional[OrderExecutor] = None

        # Agent state flags
        self._running = False
        self._shutdown_requested = False
        self._last_intraday_scan = time.monotonic()
        self._intraday_scan_thread = None

        # ML Validator, Continuous Learning, and Ticker Fetcher Subsystems
        self.ai_validator = AIValidator()
        self.continuous_learning = ContinuousLearning()
        self.ticker_fetcher = TickerFetcher()
        self._trading_db = TradingDB()
        self._current_signals = {}
        
        # XGBoost model bootstrap training check
        if config.ai.enabled and self.ai_validator.model is None:
            logger.info("XGBoost model not found. Bootstrapping initial training...")
            try:
                from ml_trainer import train_model
                train_model()
                self.ai_validator.enabled = True
                self.ai_validator._load_model()
            except Exception as e:
                logger.error("XGBoost model bootstrap training failed: %s", e)

        # Register OS-level shutdown handlers.
        signal.signal(signal.SIGINT, self.on_shutdown)
        signal.signal(signal.SIGTERM, self.on_shutdown)

        logger.info(
            "TradingAgent initialised. Universe: %d tickers.",
            len(config.universe.tickers),
        )

    # ------------------------------------------------------------------
    # Shutdown handler
    # ------------------------------------------------------------------

    def on_shutdown(self, sig: int, frame) -> None:
        """
        Handle SIGINT (Ctrl-C) and SIGTERM gracefully.

        Sets the shutdown flag so the main loop exits cleanly after the
        current scan cycle, then closes all positions and disconnects.
        """
        sig_name = "SIGINT" if sig == signal.SIGINT else "SIGTERM"
        logger.warning("Received %s — initiating graceful shutdown …", sig_name)
        self._shutdown_requested = True
        self._running = False

    # ------------------------------------------------------------------
    # Broker connection helpers
    # ------------------------------------------------------------------

    def _connect_broker(self) -> bool:
        """
        Establish Broker connection and initialise the OrderExecutor.

        Returns True on success, False on failure.
        """
        try:
            self.broker = BrokerConnector()
            self.broker.connect()
            self.executor = OrderExecutor(self.broker)
            logger.info("%s connection established.", "Alpaca" if ACTIVE_MARKET == "US" else "Zerodha")
            return True
        except ConnectionError as exc:
            logger.error("Failed to connect to broker: %s", exc)
            return False
        except Exception as exc:
            logger.error("Unexpected error connecting to broker: %s", exc, exc_info=True)
            return False

    def _disconnect_broker(self) -> None:
        """Gracefully disconnect from broker."""
        if self.broker and self.broker.is_connected():
            self.broker.disconnect()
            logger.info("Disconnected from broker.")

    # ------------------------------------------------------------------
    # Per-symbol processing
    # ------------------------------------------------------------------

    def _process_symbol(self, symbol: str) -> None:
        """
        Run the full analysis and execution pipeline for a single ticker.

        Steps
        -----
        1. Fetch OHLCV data from yfinance via PriceFeed.
        2. Compute technical trend signals via TrendEngine.
        3. Fetch sentiment score via SentimentEngine.
        4. Generate a trading decision via DecisionEngine.
        5. Execute the decision via OrderExecutor (if not HOLD).
        6. Check software-level exit conditions for existing positions.
        """
        try:
            # --- 1. Price data ---
            df = self.price_feed.get_ohlcv(symbol, period="5d", interval="5m")
            if df is None or df.empty:
                logger.warning("No OHLCV data for %s — skipping.", symbol)
                return

            current_price = self.price_feed.get_current_price(symbol)
            if (current_price is None or current_price <= 0) and self.broker is not None:
                current_price = self.broker.get_current_price(symbol)

            if current_price is None or current_price <= 0:
                logger.warning("Invalid price for %s — skipping.", symbol)
                return

            # --- 2. Trend analysis ---
            trend_signal = self.trend_engine.analyse(symbol, df)
            if trend_signal is None:
                logger.warning("Trend analysis failed for %s — skipping.", symbol)
                return

            # --- 3. Sentiment ---
            sentiment_score = self.sentiment_engine.get_sentiment(symbol)

            # Log latest headlines for audit trail (non-blocking).
            try:
                headlines = self.sentiment_engine.get_news_headlines(symbol, limit=3)
                if headlines:
                    logger.debug(
                        "Headlines for %s: %s",
                        symbol,
                        " | ".join(headlines[:3]),
                    )
            except Exception:
                pass  # Headlines are informational only; ignore errors.

            # Save signal for dashboard
            self._current_signals[symbol] = {
                "symbol": symbol,
                "price": current_price,
                "changePct": 0.0,
                "rsi": trend_signal.rsi,
                "trendScore": trend_signal.overall_trend,
                "macdSignal": trend_signal.macd_signal,
                "emaSignal": trend_signal.ema_signal,
                "adx": getattr(trend_signal, 'adx', 0.0),
                "volumeRatio": getattr(trend_signal, 'volume_ratio', 1.0),
                "signal": "HOLD"
            }

            # --- 4. Decision ---
            portfolio_state = {
                "portfolio_value": self.portfolio.portfolio_value,
                "available_funds": self.portfolio.cash,
                "open_positions": self.portfolio.open_positions,
            }

            decision: Decision = self.decision_engine.make_decision(
                symbol=symbol,
                trend_signal=trend_signal,
                sentiment_score=sentiment_score,
                current_price=current_price,
                portfolio=portfolio_state,
            )

            # --- 4.5 AI Validation ---
            decision = self.ai_validator.validate_decision(
                symbol=symbol,
                trend_signal=trend_signal,
                sentiment_score=sentiment_score,
                decision=decision,
            )

            # --- 4.6 Continuous Learning Log ---
            self.continuous_learning.log_daily_features(
                symbol=symbol,
                trend_signal=trend_signal,
                sentiment_score=sentiment_score,
                predicted_prob=decision.ml_confidence
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
                self._current_signals[symbol]["buyThreshold"] = config.signal.buy_threshold
                self._current_signals[symbol]["sellThreshold"] = config.signal.sell_threshold
                self._current_signals[symbol]["mlConfidence"] = decision.ml_confidence
                # Show reason when score is above threshold but still HOLD
                if decision.action == "HOLD" and decision.combined_score >= config.signal.buy_threshold:
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
                        pnl: Optional[float] = None
                        exit_reason: Optional[str] = None

                    if decision.action == "SELL":
                        position = self.portfolio.open_positions.get(symbol, {})
                        avg_cost = float(position.get("avg_cost", current_price))
                        qty = float(position.get("quantity", decision.quantity))
                        pnl = (current_price - avg_cost) * qty
                        exit_reason = "SELL_SIGNAL"

                    self.portfolio.record_trade(
                        symbol=symbol,
                        action=decision.action,
                        quantity=decision.quantity,
                        price=current_price,
                        pnl=pnl,
                        exit_reason=exit_reason,
                    )

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
                                trend_score=trend_signal.overall_trend,
                                combined_score=decision.combined_score,
                                headlines=trade_headlines,
                            )
                        except Exception as _le:
                            logger.debug("Learning engine update failed: %s", _le)

            # --- 6. Exit condition check for existing positions ---
            if symbol in self.portfolio.open_positions and self.executor is not None:
                position = self.portfolio.open_positions[symbol]
                exit_trigger = self.executor.check_exit_conditions(
                    symbol, current_price, position
                )

                if exit_trigger in ("STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP"):
                    logger.info(
                        "Software exit triggered for %s: %s", symbol, exit_trigger
                    )
                    qty = float(position.get("quantity", 0))
                    if qty > 0:
                        if config.agent.observe_only:
                            logger.info("[OBSERVE MODE] Would close position for %s (reason: %s), skipping.", symbol, exit_trigger)
                        else:
                            self.executor.close_position(symbol, qty)
                            avg_cost = float(position.get("avg_cost", current_price))
                        pnl = (current_price - avg_cost) * qty
                        self.portfolio.record_trade(
                            symbol=symbol,
                            action="SELL",
                            quantity=qty,
                            price=current_price,
                            pnl=pnl,
                            exit_reason=exit_trigger,
                        )

        except Exception as exc:
            # Never let a single-symbol failure crash the full scan loop.
            logger.error(
                "Unhandled exception processing %s: %s",
                symbol, exc, exc_info=True,
            )

    # ------------------------------------------------------------------
    # EOD report
    # ------------------------------------------------------------------

    def send_eod_report(self) -> None:
        """
        Generate and email the end-of-day trading report.

        Called after ``close_all_positions()`` completes, before
        ``_disconnect_broker()`` so that portfolio state is still available.
        """
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
                logger.info(
                    "EOD report emailed successfully for session %s.",
                    session_date,
                )
            else:
                logger.warning(
                    "EOD report generation succeeded but email delivery "
                    "failed for session %s — check SMTP credentials.",
                    session_date,
                )
        except Exception as exc:
            logger.error(
                "send_eod_report() failed: %s", exc, exc_info=True
            )

    # ------------------------------------------------------------------
    # EOD close-all
    # ------------------------------------------------------------------

    def _evaluate_ml_hold(self, symbol: str) -> bool:
        """Returns True if the ML model predicts a strong hold (high confidence of upward swing)."""
        if not config.ai.enabled or not getattr(self, 'ai_validator', None) or not self.ai_validator.enabled:
            return False
            
        try:
            hist_df = self.price_feed.get_daily_ohlcv(symbol, period="3mo")
            if hist_df is None or hist_df.empty:
                return False
                
            trend_signal = self.trend_engine.analyse(hist_df)
            headlines = self.sentiment_engine.get_last_headlines(symbol)
            sentiment_score = self.sentiment_engine.analyze_sentiment(symbol, headlines)
            
            # Create a dummy HOLD decision to evaluate
            decision = Decision(symbol=symbol, action="HOLD", confidence=0.5, reason="Evaluate overnight hold", quantity=0, stop_loss_price=0.0, take_profit_price=0.0, combined_score=0.0)
            validated_decision = self.ai_validator.validate_decision(symbol, trend_signal, sentiment_score, decision)
            
            # --- SENTIMENT WEIGHTING LOGIC ---
            # Base threshold is 0.65. If sentiment is positive (> 0.0), we lower the threshold 
            # to give "more weight" to the positive news. Max reduction is 0.15.
            threshold = 0.65
            if sentiment_score > 0:
                discount = min(0.15, sentiment_score * 0.15)
                threshold -= discount
                logger.info("Positive sentiment (%.2f) reduced ML hold threshold to %.2f for %s", sentiment_score, threshold, symbol)

            # If ml_confidence is high enough, we hold.
            if getattr(validated_decision, 'ml_confidence', None) is not None and validated_decision.ml_confidence >= threshold:
                return True
        except Exception as e:
            logger.warning("Failed to evaluate ML hold for %s: %s", symbol, e)
            
        return False

    def close_all_positions(self, reason: str = "EOD") -> None:
        """
        Market-sell all open positions.

        Called automatically 15 minutes before close (16:15 London time)
        or on SIGINT / SIGTERM.

        Parameters
        ----------
        reason:
            Label attached to each trade record (e.g. ``'EOD'``, ``'SHUTDOWN'``).
        """
        if not self.portfolio.open_positions:
            logger.info("close_all_positions(): no open positions to close.")
            return

        logger.info(
            "Closing all %d open positions (%s) …",
            len(self.portfolio.open_positions),
            reason,
        )

        for symbol, position in list(self.portfolio.open_positions.items()):
            qty = float(position.get("quantity", 0))
            if qty <= 0:
                continue

            # --- OVERNIGHT HOLD LOGIC ---
            if reason == "EOD" and self._evaluate_ml_hold(symbol):
                logger.info("ML model predicts overnight swing. Holding %s overnight.", symbol)
                continue


            try:
                current_price = self.price_feed.get_current_price(symbol) or 0.0
                if config.agent.observe_only:
                    logger.info("[OBSERVE MODE] Would close %s for EOD/shutdown (reason=%s), skipping.", symbol, reason)
                    continue

                if self.executor is not None:
                    success = self.executor.close_position(symbol, qty)
                else:
                    logger.warning(
                        "OrderExecutor not available — cannot close %s.", symbol
                    )
                    continue

                if success:
                    avg_cost = float(position.get("avg_cost", current_price))
                    pnl = (current_price - avg_cost) * qty if current_price > 0 else None
                    self.portfolio.record_trade(
                        symbol=symbol,
                        action="SELL",
                        quantity=qty,
                        price=current_price,
                        pnl=pnl,
                        exit_reason=reason,
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
                            sentiment_score=0.0,  # not available at EOD close
                            trend_score=0.0,
                            combined_score=0.0,
                            headlines=trade_headlines,
                        )
                    except Exception as _le:
                        logger.debug("Learning engine update (EOD close) failed: %s", _le)

            except Exception as exc:
                logger.error(
                    "Error closing position for %s: %s", symbol, exc, exc_info=True
                )

    # ------------------------------------------------------------------
    # Automated Retraining
    # ------------------------------------------------------------------
    def _check_and_run_automated_training(self):
        """
        Runs ml_trainer.py daily when the market is closed to ensure the
        model stays up-to-date with the latest daily candles.
        """
        import os
        from datetime import datetime, timedelta

        # Ensure we only run this once per day post-market
        tracker_file = f"data/last_ml_training_{ACTIVE_MARKET}.txt"
        
        # If in docker, it maps to /app/data
        if os.environ.get("TRADES_CSV_PATH") is not None or os.path.exists("/.dockerenv"):
            tracker_file = f"/app/data/last_ml_training_{ACTIVE_MARKET}.txt"
            
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # Check if already trained today
        if os.path.exists(tracker_file):
            with open(tracker_file, "r") as f:
                last_train = f.read().strip()
                if last_train == today_str:
                    return # Already trained today
                    
        logger.info("Executing daily automated ML model retraining...")
        try:
            from ml_trainer import train_model
            success = train_model()
            if success:
                logger.info("Automated ML retraining completed successfully.")
                with open(tracker_file, "w") as f:
                    f.write(today_str)
                # Reload the newly trained model
                self.ai_validator.reload_model()
            else:
                logger.error("Automated ML retraining failed or aborted.")
        except Exception as e:
            logger.error("Error during automated ML retraining: %s", e)

    def _write_system_status(self, is_market_open: bool, agent_status: str, next_open: str = "") -> None:
        """Write the current market and agent status to a JSON file for the dashboard."""
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
                "timestamp": time.time()
            }
            with open(out_path, "w") as f:
                json.dump(status_data, f, indent=2)
        except Exception as e:
            logger.error("Failed to write system status: %s", e)

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Main execution loop.

        Flow
        ----
        1. If the market is closed, log time-to-open and sleep until 08:00.
        2. Connect to IBKR (paper port 7497).
        3. Every ``loop_interval`` seconds:
           a. Sync portfolio state from IBKR.
           b. Check daily loss limit — halt trading if breached.
           c. Scan every ticker: fetch → analyse → decide → execute.
           d. Log portfolio summary.
           e. Sleep for remainder of the interval.
        4. If within the EOD close window (16:15), close all positions and exit.
        5. On normal session end or shutdown signal, disconnect and log summary.
        """
        logger.info("=" * 70)
        logger.info("%s Trading Agent (%s) — starting up", config.market.exchange, ACTIVE_MARKET)
        logger.info("=" * 70)

        # Start the ticker fetcher immediately so the dashboard gets data
        self.ticker_fetcher.start()

        # --- Initial portfolio snapshot for dashboard ---
        # Connect briefly to fetch NAV/cash so the dashboard shows real
        # numbers even while the agent waits for market open.
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
                    secs,
                    secs / 60,
                )
                # Sleep in chunks so SIGINT is handled promptly.
                slept = 0.0
                while slept < secs and not self._shutdown_requested:
                    self._write_system_status(False, "sleeping", self.session.next_open_time())
                    # Run automated ML retraining if needed
                    self._check_and_run_automated_training()

                    # Check if we should run the pre-market scanner
                    if self.session.is_pre_market() and not getattr(self, "_scanner_run_today", False):
                        logger.info("Pre-market window detected. Running sector scanner...")
                        try:
                            import sector_scanner
                            sector_scanner.run_scanner()
                            self._scanner_run_today = True
                        except Exception as e:
                            logger.error("Sector scanner failed: %s", e)

                    # --- PRE-MARKET OVERNIGHT PROTECTION ---
                    # If we hold positions overnight, wake up and monitor them in the pre-market
                    if self.session.is_pre_market() and getattr(self.portfolio, 'open_positions', None):
                        last_pm_check = getattr(self, "_last_pm_check", 0)
                        # Check once every 2 minutes so we don't spam the price feed API
                        if time.monotonic() - last_pm_check > 120:
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
                                        
                                        # If the stock is dumping more than 2% in the pre-market, check sentiment
                                        if drop_pct <= -0.02:
                                            # If news is negative, DUMP IT immediately before regular open!
                                            sent_score = self.sentiment_engine.get_sentiment(symbol)
                                            if sent_score < 0:
                                                logger.warning("PRE-MARKET DUMP TRIGGERED: %s dropped %.2f%% and news is negative (%.2f). Selling!", symbol, drop_pct * 100, sent_score)
                                                if getattr(self, "executor", None):
                                                    # Make sure broker is connected
                                                    if not self._broker_connected:
                                                        self._connect_broker()
                                                    success = self.executor.close_position(symbol, qty, outsideRth=True)
                                                    if success:
                                                        pnl = (current_price - avg_cost) * qty
                                                        self.portfolio.record_trade(
                                                            symbol=symbol, action="SELL", quantity=qty, price=current_price,
                                                            pnl=pnl, exit_reason="PRE_MARKET_DUMP"
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
            logger.critical(
                "Cannot establish broker connection — aborting agent run."
            )
            return

        # --- Step 3: Main scan loop ---
        self._running = True
        loop_interval = config.agent.loop_interval_seconds

        try:
            while self._running and not self._shutdown_requested:
                loop_start = time.monotonic()

                # Check market status; exit loop if session ended.
                if not self.session.is_market_open():
                    logger.info("Market session has ended — exiting scan loop.")
                    self._write_system_status(False, "sleeping", self.session.next_open_time())
                    break

                self._write_system_status(True, "running")

                # Launch intra-day background scan if interval elapsed
                if time.monotonic() - self._last_intraday_scan > config.agent.intraday_scan_interval_minutes * 60:
                    self._last_intraday_scan = time.monotonic()
                    if self._intraday_scan_thread is None or not self._intraday_scan_thread.is_alive():
                        logger.info("Triggering background Intraday Sector Scan...")
                        def run_intraday_scan():
                            try:
                                import sector_scanner
                                sector_scanner.run_scanner()
                            except Exception as e:
                                logger.error("Intraday sector scanner failed: %s", e)
                        
                        self._intraday_scan_thread = threading.Thread(target=run_intraday_scan, daemon=True)
                        self._intraday_scan_thread.start()

                # a. Sync portfolio from broker
                try:
                    self.portfolio.update(self.broker)
                    self.executor.sync_positions(self.portfolio.open_positions, self.broker)
                except Exception as exc:
                    logger.error(
                        "Portfolio update failed: %s", exc, exc_info=True
                    )

                # b. Daily loss limit check
                if self.portfolio.check_daily_loss_limit():
                    logger.warning(
                        "Daily loss limit breached — ceasing all trading for today."
                    )
                    break

                # c. EOD close check (within 15 min of close)
                if self.session.is_near_close():
                    logger.info(
                        "Approaching session close (%.1f min remaining) — "
                        "closing all open positions.",
                        self.session.minutes_remaining(),
                    )
                    self.close_all_positions(reason="EOD")
                    
                    # Trigger EOD model learning
                    try:
                        self.continuous_learning.retrain_model_if_needed()
                    except Exception as exc:
                        logger.error("Continuous learning retrain failed: %s", exc)
                        
                    # Wait for session to actually close then exit.
                    remaining_secs = self.session.minutes_remaining() * 60
                    logger.info(
                        "Waiting %.0f s for session close …", remaining_secs
                    )
                    time.sleep(max(0, remaining_secs + 5))
                    break

                # d. Per-symbol scan (using daily targets from sector scanner if available)
                try:
                    data_dir = os.path.dirname(config.agent.trades_csv)
                    targets_file = os.path.join(data_dir, "daily_targets.json")
                    daily_targets = config.universe.tickers
                    if os.path.exists(targets_file):
                        with open(targets_file, "r") as f:
                            parsed_targets = json.load(f)
                            if parsed_targets and isinstance(parsed_targets, list):
                                # Combine core universe and pre-market targets, removing duplicates
                                combined = set(daily_targets + parsed_targets)
                                daily_targets = list(combined)
                                # Sort to maintain some stable ordering
                                daily_targets.sort()
                except Exception as exc:
                    logger.warning("Failed to load daily_targets.json, falling back to config: %s", exc)
                    daily_targets = config.universe.tickers

                logger.info(
                    "--- Scan loop | %s | %d tickers | %.1f min remaining ---",
                    self.session.get_session_date(),
                    len(daily_targets),
                    self.session.minutes_remaining(),
                )

                for symbol in daily_targets:
                    if self._shutdown_requested:
                        break
                    self._process_symbol(symbol)

                # Dump signals for dashboard
                try:
                    signals_list = list(self._current_signals.values())
                    self._trading_db.upsert_signals(signals_list)
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
            logger.critical(
                "Unhandled exception in main loop: %s", exc, exc_info=True
            )

        finally:
            self._write_system_status(False, "offline")
            # --- Cleanup ---
            if self._shutdown_requested:
                if config.agent.liquidate_on_shutdown:
                    logger.info("Shutdown signal received — liquidating all open positions.")
                    self.close_all_positions(reason="SHUTDOWN")
                else:
                    logger.info("Shutdown signal received — liquidate_on_shutdown is False, keeping positions open.")

            # Log final performance stats.
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

            # Only send EOD report if market is closed or near close.
            # Avoids spurious emails on mid-day container restarts.
            if not self.session.is_market_open() or self.session.is_near_close():
                self.send_eod_report()
            else:
                logger.info(
                    "Skipping EOD report — market still open (%.1f min remaining). "
                    "This appears to be a mid-session restart.",
                    self.session.minutes_remaining(),
                )
            self.ticker_fetcher.stop()
            self._disconnect_broker()
            logger.info("=" * 70)
            logger.info("TradingAgent shutdown complete.")
            logger.info("=" * 70)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent = TradingAgent()
    agent.run()
