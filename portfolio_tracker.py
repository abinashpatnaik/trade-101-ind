"""
portfolio_tracker.py
====================
Maintains a real-time view of the agent's portfolio, tracks P&L,
and persists trade history to CSV.

Syncs with IBKR account data on demand via IBKRConnector and applies
the daily-loss-limit rule to protect against runaway drawdowns.

Outputs
-------
- In-memory state (portfolio_value, cash, open_positions, daily_pnl, etc.)
- trades.csv: append-mode CSV log of every completed trade
"""

from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from config import config, CUR_SYM

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Represents a single completed or partially completed trade."""

    date: str
    time: str
    symbol: str
    action: str          # 'BUY' | 'SELL'
    quantity: int
    price: float
    notional: float      # quantity × price
    pnl: Optional[float] = None   # realised P&L for SELL trades
    exit_reason: Optional[str] = None  # 'SELL_SIGNAL' | 'STOP_LOSS' | 'TAKE_PROFIT' | 'EOD'


class PortfolioTracker:
    """
    Tracks portfolio state and trade history for the FTSE 100 trading agent.

    This class:
      1. Syncs account and position data from IBKR.
      2. Computes daily P&L and enforces the daily-loss limit.
      3. Logs every trade to CSV for post-session analysis.
      4. Provides performance statistics (win rate, total P&L, etc.).

    Usage
    -----
    >>> tracker = PortfolioTracker()
    >>> tracker.update(ibkr_connector)
    >>> if tracker.check_daily_loss_limit():
    ...     print("Daily loss limit reached — stop trading")
    >>> summary = tracker.get_summary()
    """

    def __init__(self) -> None:
        self._risk = config.risk
        self._wallet = config.wallet

        # Portfolio state
        self.portfolio_value: float = 0.0      # Total NAV (INR)
        self.cash: float = 0.0                 # Available funds (INR)
        self.open_positions: Dict[str, Dict] = {}
        self.daily_pnl: float = 0.0            # P&L since session start (INR)

        # Session tracking
        self._session_start_nav: Optional[float] = None
        self._session_date: str = str(date.today())

        # Wallet / daily spend tracking
        self.daily_spent: float = 0.0          # Total BUY notional today (INR)
        self.daily_realised_profit: float = 0.0  # Profits from closed trades today

        # Trade history (in-memory; also persisted to CSV)
        self.closed_trades: List[TradeRecord] = []
        self._trades_csv_path: str = config.agent.trades_csv

        # Ensure the CSV file exists with headers.
        self._init_csv()

        logger.info(
            "PortfolioTracker initialised — daily spend cap: ₹%.2f reinvest: %s",
            self._wallet.daily_spend_cap,
            self._wallet.reinvest_profits,
        )

    # ------------------------------------------------------------------
    # CSV management
    # ------------------------------------------------------------------

    def _init_csv(self) -> None:
        """Create the trades CSV with a header row if it doesn't exist."""
        csv_dir = os.path.dirname(self._trades_csv_path)
        if csv_dir and not os.path.exists(csv_dir):
            os.makedirs(csv_dir, exist_ok=True)

        if not os.path.exists(self._trades_csv_path):
            with open(self._trades_csv_path, mode="w", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "date", "time", "symbol", "action", "quantity",
                        "price", "notional", "pnl", "exit_reason",
                    ],
                )
                writer.writeheader()
            logger.info("Trade log CSV created: %s", self._trades_csv_path)

    def _append_to_csv(self, trade: TradeRecord) -> None:
        """Append a single trade record to the CSV file."""
        try:
            with open(self._trades_csv_path, mode="a", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "date", "time", "symbol", "action", "quantity",
                        "price", "notional", "pnl", "exit_reason",
                    ],
                )
                writer.writerow(
                    {
                        "date": trade.date,
                        "time": trade.time,
                        "symbol": trade.symbol,
                        "action": trade.action,
                        "quantity": trade.quantity,
                        "price": trade.price,
                        "notional": round(trade.notional, 2),
                        "pnl": round(trade.pnl, 2) if trade.pnl is not None else "",
                        "exit_reason": trade.exit_reason or "",
                    }
                )
        except OSError as exc:
            logger.error("Failed to write trade to CSV: %s", exc, exc_info=True)

    def _dump_local_positions(self) -> None:
        """Dump the local open_positions to a JSON file for the dashboard."""
        try:
            data_dir = os.path.dirname(config.agent.trades_csv)
            out_path = os.path.join(data_dir, "local_positions.json")
            with open(out_path, "w") as f:
                json.dump(self.open_positions, f, indent=2)
        except OSError as exc:
            logger.error("Failed to dump local positions: %s", exc, exc_info=True)

    def _dump_local_summary(self) -> None:
        """Dump the local summary to a JSON file for the dashboard."""
        try:
            data_dir = os.path.dirname(config.agent.trades_csv)
            out_path = os.path.join(data_dir, "local_summary.json")
            summary_data = self.get_summary()
            with open(out_path, "w") as f:
                json.dump(summary_data, f, indent=2)
        except OSError as exc:
            logger.error("Failed to dump local summary: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def update(self, ibkr_connector) -> None:
        """
        Synchronise portfolio state from IBKR.

        Fetches account summary (NAV, available funds, daily P&L) and
        current positions.  Sets the session start NAV on first call.

        Parameters
        ----------
        ibkr_connector:
            Connected IBKRConnector instance.
        """
        try:
            summary = ibkr_connector.get_account_summary()
            positions = ibkr_connector.get_positions()

            self.portfolio_value = summary.get("NetLiquidation", self.portfolio_value)
            self.cash = summary.get("AvailableFunds", self.cash)
            self.daily_pnl = summary.get("DailyPnL", self.daily_pnl)
            if positions is not None:
                self.open_positions = positions

            # Capture session-start NAV on the first update each trading day.
            today = str(date.today())
            if self._session_start_nav is None or today != self._session_date:
                self._session_start_nav = self.portfolio_value
                self._session_date = today
                logger.info(
                    "Session start NAV recorded: %s%.2f", CUR_SYM, self._session_start_nav
                )

            logger.debug(
                "Portfolio updated: nav=₹%.2f cash=₹%.2f "
                "open_positions=%d daily_pnl=₹%.2f",
                self.portfolio_value,
                self.cash,
                len(self.open_positions),
                self.daily_pnl,
            )
            
            self._dump_local_positions()
            self._dump_local_summary()

        except Exception as exc:
            logger.error(
                "PortfolioTracker.update() failed: %s", exc, exc_info=True
            )

    # ------------------------------------------------------------------
    # Risk checks
    # ------------------------------------------------------------------

    def check_daily_loss_limit(self) -> bool:
        """
        Return True if the daily loss has exceeded the configured threshold,
        meaning the agent should cease trading for the remainder of the session.

        The check is:
            (session_start_nav - portfolio_value) / session_start_nav >= max_daily_loss_pct

        Returns False if the session start NAV has not yet been recorded.
        """
        if self._session_start_nav is None or self._session_start_nav <= 0:
            return False

        loss_pct = (
            (self._session_start_nav - self.portfolio_value)
            / self._session_start_nav
        )

        if loss_pct >= self._risk.max_daily_loss_pct:
            logger.warning(
                "DAILY LOSS LIMIT REACHED: loss_pct=%.2f%% >= max=%.2f%% "
                "(start=₹%.2f, current=₹%.2f).",
                loss_pct * 100,
                self._risk.max_daily_loss_pct * 100,
                self._session_start_nav,
                self.portfolio_value,
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Trade recording
    # ------------------------------------------------------------------

    def record_trade(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float,
        pnl: Optional[float] = None,
        exit_reason: Optional[str] = None,
    ) -> None:
        """
        Log a completed trade to the in-memory list and to CSV.

        Parameters
        ----------
        symbol:
            Bare LSE ticker.
        action:
            ``'BUY'`` or ``'SELL'``.
        quantity:
            Number of shares traded.
        price:
            Execution price in INR.
        pnl:
            Realised P&L for SELL trades (entry notional - exit notional).
            Pass None for BUY entries.
        exit_reason:
            Optional label: ``'SELL_SIGNAL'``, ``'STOP_LOSS'``,
            ``'TAKE_PROFIT'``, ``'EOD'``.
        """
        from datetime import datetime

        now = datetime.now()
        trade = TradeRecord(
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H:%M:%S"),
            symbol=symbol,
            action=action.upper(),
            quantity=quantity,
            price=price,
            notional=quantity * price,
            pnl=pnl,
            exit_reason=exit_reason,
        )

        notional = quantity * price

        if action.upper() == "BUY":
            # Track daily spend against the cap
            self.daily_spent += notional
            logger.info(
                "Wallet | daily_spent=₹%.2f / cap=₹%.2f (%.1f%% used)",
                self.daily_spent,
                self._wallet.daily_spend_cap,
                (self.daily_spent / self._wallet.daily_spend_cap * 100)
                if self._wallet.daily_spend_cap > 0 else 0,
            )
            
            # Update local portfolio state for offline dashboard support
            if symbol not in self.open_positions:
                self.open_positions[symbol] = {"quantity": 0, "avg_cost": 0.0, "market_value": 0.0}
            pos = self.open_positions[symbol]
            old_qty = int(pos.get("quantity", 0))
            old_cost = float(pos.get("avg_cost", 0.0))
            new_qty = old_qty + quantity
            if new_qty > 0:
                pos["avg_cost"] = ((old_qty * old_cost) + (quantity * price)) / new_qty
            pos["quantity"] = new_qty
            pos["market_value"] = new_qty * price
            self.cash -= notional

        if action.upper() == "SELL":
            self.closed_trades.append(trade)
            # Track realised profits for reinvestment
            if pnl is not None and pnl > 0:
                self.daily_realised_profit += pnl
                if self._wallet.reinvest_profits:
                    logger.info(
                        "Wallet | profit ₹%.2f from %s added to reinvestment pool — "
                        "total reinvestable today: ₹%.2f",
                        pnl, symbol, self.daily_realised_profit,
                    )
            
            # Update local portfolio state for offline dashboard support
            if symbol in self.open_positions:
                pos = self.open_positions[symbol]
                old_qty = int(pos.get("quantity", 0))
                new_qty = max(0, old_qty - quantity)
                pos["quantity"] = new_qty
                pos["market_value"] = new_qty * price
                if new_qty == 0:
                    self.open_positions.pop(symbol)
            self.cash += notional
            if pnl is not None:
                self.daily_pnl += pnl

        # Keep portfolio value in sync
        pos_val = sum(float(pos.get("market_value", 0.0)) for pos in self.open_positions.values())
        self.portfolio_value = self.cash + pos_val

        self._append_to_csv(trade)

        logger.info(
            "Trade recorded: %s %s %d @ ₹%.4f notional=₹%.2f pnl=%s reason=%s",
            action, symbol, quantity, price,
            trade.notional,
            f"{CUR_SYM}{pnl:.2f}" if pnl is not None else "N/A",
            exit_reason or "N/A",
        )
        
        self._dump_local_positions()
        self._dump_local_summary()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_summary(self) -> Dict:
        """
        Return a formatted dict with the current portfolio state.

        Returns
        -------
        dict
            Keys: portfolio_value, cash, daily_pnl, open_positions_count,
            open_positions, session_start_nav, daily_loss_pct.
        """
        daily_loss_pct = 0.0
        if self._session_start_nav and self._session_start_nav > 0:
            daily_loss_pct = (
                (self._session_start_nav - self.portfolio_value)
                / self._session_start_nav
            ) * 100

        # Effective daily budget = cap + reinvested profits
        effective_cap = self._wallet.daily_spend_cap
        if self._wallet.reinvest_profits:
            effective_cap += self.daily_realised_profit
        remaining_budget = max(0.0, effective_cap - self.daily_spent)

        return {
            "portfolio_value": round(self.portfolio_value, 2),
            "cash": round(self.cash, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_loss_pct": round(daily_loss_pct, 3),
            "open_positions_count": len(self.open_positions),
            "open_positions": {
                sym: {
                    "quantity": pos.get("quantity"),
                    "avg_cost": round(float(pos.get("avg_cost", 0)), 4),
                    "market_value": round(float(pos.get("market_value", 0)), 2),
                }
                for sym, pos in self.open_positions.items()
            },
            "session_start_nav": round(self._session_start_nav or 0, 2),
            "session_date": self._session_date,
            # Wallet summary
            "daily_spend_cap": round(self._wallet.daily_spend_cap, 2),
            "daily_spent": round(self.daily_spent, 2),
            "daily_realised_profit": round(self.daily_realised_profit, 2),
            "remaining_budget": round(remaining_budget, 2),
            "budget_exhausted": remaining_budget < self._wallet.min_trade_value,
        }

    def get_performance(self) -> Dict:
        """
        Compute performance statistics from the closed-trade history.

        Returns
        -------
        dict
            Keys: num_trades, win_rate, total_pnl, best_trade, worst_trade,
            avg_pnl_per_trade.
        """
        if not self.closed_trades:
            return {
                "num_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
                "avg_pnl_per_trade": 0.0,
            }

        pnl_values = [
            t.pnl for t in self.closed_trades if t.pnl is not None
        ]

        if not pnl_values:
            return {
                "num_trades": len(self.closed_trades),
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
                "avg_pnl_per_trade": 0.0,
            }

        winners = [p for p in pnl_values if p > 0]
        total_pnl = sum(pnl_values)
        num_trades = len(pnl_values)
        win_rate = len(winners) / num_trades if num_trades > 0 else 0.0

        return {
            "num_trades": num_trades,
            "win_rate": round(win_rate * 100, 2),   # percentage
            "total_pnl": round(total_pnl, 2),
            "best_trade": round(max(pnl_values), 2),
            "worst_trade": round(min(pnl_values), 2),
            "avg_pnl_per_trade": round(total_pnl / num_trades, 2),
        }

    def __repr__(self) -> str:
        return (
            f"<PortfolioTracker "
            f"nav={CUR_SYM}{self.portfolio_value:.2f} "
            f"cash={CUR_SYM}{self.cash:.2f} "
            f"positions={len(self.open_positions)} "
            f"daily_pnl={CUR_SYM}{self.daily_pnl:.2f}>"
        )
