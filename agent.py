"""
agent.py
========
Main orchestrator for the FTSE 100 automated trading agent.

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
  3. Scan all 20 FTSE 100 tickers every 60 seconds.
  4. At 16:15 (15 min before close) close all open positions.
  5. Disconnect gracefully and log the session summary.

Signals SIGINT (Ctrl-C) and SIGTERM both trigger a graceful shutdown that
closes all open positions before exiting.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import sys
import time
from typing import Optional

from config import config
from decision_engine import DecisionEngine, Decision
from zerodha_connector import ZerodhaConnector as IBKRConnector
from market_session import MarketSession
from order_executor import OrderExecutor
from portfolio_tracker import PortfolioTracker
from price_feed import PriceFeed
from report_generator import EODReportGenerator
from report_sender import ReportSender
from learning_engine import LearningEngine
from sentiment_engine import SentimentEngine
from trend_engine import TrendEngine

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
    Top-level orchestrator for the FTSE 100 automated trading agent.

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

        # IBKR connector and order executor (connected in run())
        self.ibkr: Optional[IBKRConnector] = None
        self.executor: Optional[OrderExecutor] = None

        # Agent state flags
        self._running = False
        self._shutdown_requested = False

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
    # IBKR connection helpers
    # ------------------------------------------------------------------

    def _connect_ibkr(self) -> bool:
        """
        Establish IBKR connection and initialise the OrderExecutor.

        Returns True on success, False on failure.
        """
        try:
            self.ibkr = IBKRConnector()
            self.ibkr.connect()
            self.executor = OrderExecutor(self.ibkr)
            logger.info("Zerodha connection established.")
            return True
        except ConnectionError as exc:
            logger.error("Failed to connect to Zerodha: %s", exc)
            return False
        except Exception as exc:
            logger.error(
                "Unexpected error connecting to Zerodha: %s", exc, exc_info=True
            )
            return False

    def _disconnect_ibkr(self) -> None:
        """Gracefully disconnect from IBKR."""
        if self.ibkr and self.ibkr.is_connected():
            self.ibkr.disconnect()
            logger.info("Disconnected from Zerodha.")

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

            current_price = None
            if self.ibkr is not None:
                current_price = self.ibkr.get_current_price(symbol)
            if current_price is None or current_price <= 0:
                current_price = self.price_feed.get_current_price(symbol)

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

            logger.info(
                "Decision — %s: action=%s confidence=%.3f score=%.3f | %s",
                symbol,
                decision.action,
                decision.confidence,
                decision.combined_score,
                decision.reason[:120],
            )

            # --- 5. Execute ---
            if decision.action != "HOLD" and self.executor is not None:
                success = self.executor.execute(decision, symbol, current_price)
                if success and decision.action in ("BUY", "SELL"):
                    pnl: Optional[float] = None
                    exit_reason: Optional[str] = None

                    if decision.action == "SELL":
                        position = self.portfolio.open_positions.get(symbol, {})
                        avg_cost = float(position.get("avg_cost", current_price))
                        qty = int(position.get("quantity", decision.quantity))
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

                if exit_trigger in ("STOP_LOSS", "TAKE_PROFIT"):
                    logger.info(
                        "Software exit triggered for %s: %s", symbol, exit_trigger
                    )
                    qty = int(position.get("quantity", 0))
                    if qty > 0:
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
        ``_disconnect_ibkr()`` so that portfolio state is still available.
        """
        try:
            session_date = self.session.get_session_date()
            summary = self.portfolio.get_summary()
            performance = self.portfolio.get_performance()

            report = self.report_gen.generate(
                session_date=session_date,
                portfolio_summary=summary,
                performance=performance,
                trades_csv_path=config.agent.trades_csv,
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
            qty = int(position.get("quantity", 0))
            if qty <= 0:
                continue

            try:
                current_price = self.price_feed.get_current_price(symbol) or 0.0
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
        logger.info("NSE Nifty 50 Trading Agent — starting up")
        logger.info("=" * 70)

        # --- Step 1: Wait for market open ---
        if not self.session.is_market_open():
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
                    chunk = min(30.0, secs - slept)
                    time.sleep(chunk)
                    slept += chunk

        if self._shutdown_requested:
            logger.info("Shutdown requested before market open — exiting.")
            return

        # --- Step 2: Connect to IBKR ---
        if not self._connect_ibkr():
            logger.critical(
                "Cannot establish Zerodha connection — aborting agent run."
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
                    break

                # a. Sync portfolio from IBKR
                try:
                    self.portfolio.update(self.ibkr)
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
                    # Wait for session to actually close then exit.
                    remaining_secs = self.session.minutes_remaining() * 60
                    logger.info(
                        "Waiting %.0f s for session close …", remaining_secs
                    )
                    time.sleep(max(0, remaining_secs + 5))
                    break

                # d. Per-symbol scan
                logger.info(
                    "--- Scan loop | %s | %d tickers | %.1f min remaining ---",
                    self.session.get_session_date(),
                    len(config.universe.tickers),
                    self.session.minutes_remaining(),
                )

                for symbol in config.universe.tickers:
                    if self._shutdown_requested:
                        break
                    self._process_symbol(symbol)

                # e. Portfolio summary log
                summary = self.portfolio.get_summary()
                logger.info(
                    "Portfolio: nav=₹%.2f cash=₹%.2f positions=%d daily_pnl=₹%.2f (%.3f%%)",
                    summary["portfolio_value"],
                    summary["cash"],
                    summary["open_positions_count"],
                    summary["daily_pnl"],
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
            # --- Cleanup ---
            if self._shutdown_requested:
                logger.info("Shutdown signal received — closing all open positions.")
                self.close_all_positions(reason="SHUTDOWN")

            # Log final performance stats.
            perf = self.portfolio.get_performance()
            logger.info(
                "Session performance: trades=%d win_rate=%.1f%% "
                "total_pnl=₹%.2f best=₹%.2f worst=₹%.2f",
                perf["num_trades"],
                perf["win_rate"],
                perf["total_pnl"],
                perf["best_trade"],
                perf["worst_trade"],
            )

            self.send_eod_report()
            self._disconnect_ibkr()
            logger.info("=" * 70)
            logger.info("TradingAgent shutdown complete.")
            logger.info("=" * 70)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent = TradingAgent()
    agent.run()
