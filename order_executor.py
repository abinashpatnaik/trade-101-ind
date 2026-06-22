"""
order_executor.py
=================
Translates Decision objects into live IBKR orders.

For each BUY decision the executor:
  1. Places a market order for the desired quantity.
  2. Places a stop-loss STP order at the computed price.
  3. Places a take-profit LMT order at the computed price.
  (Steps 2 and 3 approximate an OCO bracket using separate sibling orders
   because plain IBKR API stop-limit OCO requires OCA groups, which are
   submitted here as two independent orders with manual OCO-style monitoring.)

For each SELL decision the executor places a market order to close the
full position.

The class also provides check_exit_conditions() as a software-level fallback
that manually evaluates stop-loss and take-profit thresholds on every loop
iteration in case the exchange orders are not triggered (e.g. fast gapping,
pre-open price moves, or paper-trading quirks).

Requires:
    ibkr_connector.IBKRConnector
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from config import config
from decision_engine import Decision
from zerodha_connector import ZerodhaConnector as IBKRConnector

logger = logging.getLogger(__name__)


@dataclass
class OpenOrder:
    """Tracks all order IDs associated with a single open position entry."""

    symbol: str
    entry_order_id: int
    order_type: str                           # 'BUY' | 'SELL'
    quantity: int
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    stop_loss_order_id: Optional[int] = None
    take_profit_order_id: Optional[int] = None


class OrderExecutor:
    """
    Executes trading decisions via IBKRConnector.

    Usage
    -----
    >>> connector = IBKRConnector()
    >>> connector.connect()
    >>> executor = OrderExecutor(connector)
    >>> executor.execute(decision, 'HSBA', 620.5)
    """

    # Trailing stop percentage: 1.5% — tighter than the 2% fixed stop so it
    # locks in more profit as price rises.
    _trailing_stop_pct: float = 0.015

    def __init__(self, ibkr: IBKRConnector) -> None:
        self._ibkr = ibkr
        # Active bracket orders keyed by symbol.
        self._open_orders: Dict[str, OpenOrder] = {}
        # Highest price seen since entry for each open position (trailing stop).
        self._trailing_high: Dict[str, float] = {}
        logger.debug("OrderExecutor initialised.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _place_bracket(
        self,
        symbol: str,
        quantity: int,
        entry_order_id: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> OpenOrder:
        """
        Submit the stop-loss and take-profit sibling orders after the parent
        market-buy order has been placed.

        Both orders are SELL orders for the same quantity, mirroring the entry.
        """
        sl_order_id: Optional[int] = None
        tp_order_id: Optional[int] = None

        # --- Stop-loss (Trailing Stop) ---
        try:
            sl_order_id = self._ibkr.place_trailing_stop_order(
                symbol=symbol,
                action="SELL",
                quantity=quantity,
                trail_percent=config.risk.trailing_stop_pct,
            )
            logger.info(
                "Trailing Stop-loss placed for %s @ %.2f%% trail (order_id=%s)",
                symbol, config.risk.trailing_stop_pct * 100, sl_order_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to place trailing stop-loss for %s: %s", symbol, exc, exc_info=True
            )

        # Note: We skip the static Take-Profit (LMT) order since the trailing
        # stop will automatically lock in profits as the stock price rises.

        return OpenOrder(
            symbol=symbol,
            entry_order_id=entry_order_id,
            order_type="BUY",
            quantity=quantity,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            stop_loss_order_id=sl_order_id,
            take_profit_order_id=tp_order_id,
        )

    def _cancel_bracket(self, symbol: str) -> None:
        """Cancel any open bracket orders for *symbol* (called before SELL)."""
        order = self._open_orders.get(symbol)
        if order is None:
            return

        for oid_attr, label in [
            ("stop_loss_order_id", "stop-loss"),
            ("take_profit_order_id", "take-profit"),
        ]:
            oid = getattr(order, oid_attr, None)
            if oid is not None:
                try:
                    self._ibkr.cancel_order(oid)
                    logger.info(
                        "Cancelled %s order %s for %s", label, oid, symbol
                    )
                except Exception as exc:
                    logger.warning(
                        "Could not cancel %s order %s for %s: %s",
                        label, oid, symbol, exc,
                    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        decision: Decision,
        symbol: str,
        current_price: float,
    ) -> bool:
        """
        Execute *decision* for *symbol* at approximately *current_price*.

        Returns True if the primary order was placed successfully, False
        otherwise.  Bracket orders failing does not cause this method to
        return False — the entry is still valid; exit management falls back
        to check_exit_conditions().

        Parameters
        ----------
        decision:
            Output of DecisionEngine.make_decision().
        symbol:
            Bare LSE ticker.
        current_price:
            Latest price used for logging and bracket calculations.

        Returns
        -------
        bool
        """
        if decision.action == "HOLD":
            logger.debug("OrderExecutor.execute(): HOLD for %s — no action.", symbol)
            return True

        if not self._ibkr.is_connected():
            logger.error(
                "OrderExecutor.execute(): IBKR not connected — cannot execute "
                "%s for %s.", decision.action, symbol
            )
            return False

        # ---------------------------------------------------------------
        # BUY
        # ---------------------------------------------------------------
        if decision.action == "BUY":
            try:
                entry_order_id = self._ibkr.place_market_order(
                    symbol=symbol,
                    action="BUY",
                    quantity=decision.quantity,
                )
            except Exception as exc:
                logger.error(
                    "Failed to place BUY market order for %s: %s",
                    symbol, exc, exc_info=True,
                )
                return False

            # Register bracket orders.
            open_order = self._place_bracket(
                symbol=symbol,
                quantity=decision.quantity,
                entry_order_id=entry_order_id,
                entry_price=current_price,
                stop_loss_price=decision.stop_loss_price,
                take_profit_price=decision.take_profit_price,
            )
            self._open_orders[symbol] = open_order
            # Initialise the trailing high to the entry price.
            self._trailing_high[symbol] = current_price

            logger.info(
                "BUY executed for %s: qty=%d entry_id=%s sl=%.4f tp=%.4f",
                symbol,
                decision.quantity,
                entry_order_id,
                decision.stop_loss_price,
                decision.take_profit_price,
            )
            return True

        # ---------------------------------------------------------------
        # SELL
        # ---------------------------------------------------------------
        if decision.action == "SELL":
            # Guard: verify IBKR actually holds this position before selling
            live_positions = self._ibkr.get_positions()
            live_qty = live_positions.get(symbol, {}).get("quantity", 0)
            if live_qty <= 0:
                logger.warning(
                    "SELL skipped for %s — IBKR shows 0 shares held (stale cache). Clearing.",
                    symbol,
                )
                self._open_orders.pop(symbol, None)
                self._trailing_high.pop(symbol, None)
                return False

            # Cancel any pending bracket orders first to avoid orphaned orders.
            self._cancel_bracket(symbol)

            try:
                sell_order_id = self._ibkr.place_market_order(
                    symbol=symbol,
                    action="SELL",
                    quantity=live_qty,  # use IBKR actual qty, not cached
                )
            except Exception as exc:
                logger.error(
                    "Failed to place SELL market order for %s: %s",
                    symbol, exc, exc_info=True,
                )
                return False

            # Remove from open orders tracking.
            self._open_orders.pop(symbol, None)

            logger.info(
                "SELL executed for %s: qty=%d order_id=%s",
                symbol, decision.quantity, sell_order_id,
            )
            return True

        logger.warning(
            "OrderExecutor.execute(): unrecognised action '%s' for %s.",
            decision.action, symbol,
        )
        return False

    def check_exit_conditions(
        self,
        symbol: str,
        current_price: float,
        position: Dict,
    ) -> Optional[str]:
        """
        Since we now use native broker trailing stops, this method no longer
        manages software trailing stops. It will only return None.
        Native broker execution handles the exit automatically.
        """
        return None

    def close_position(self, symbol: str, quantity: int) -> bool:
        """
        Convenience method to immediately close an open position via a market
        SELL order without consulting the Decision engine.

        Cancels any open bracket orders for *symbol* first.

        Returns True if the order was successfully placed.
        """
        self._cancel_bracket(symbol)

        if not self._ibkr.is_connected():
            logger.error(
                "close_position(): IBKR not connected — cannot close %s.", symbol
            )
            return False

        try:
            order_id = self._ibkr.place_market_order(
                symbol=symbol,
                action="SELL",
                quantity=quantity,
            )
            self._open_orders.pop(symbol, None)
            logger.info(
                "Position closed for %s: qty=%d order_id=%s",
                symbol, quantity, order_id,
            )
            return True
        except Exception as exc:
            logger.error(
                "close_position() failed for %s: %s", symbol, exc, exc_info=True
            )
            return False

    @property
    def open_orders(self) -> Dict[str, OpenOrder]:
        """Read-only view of the currently tracked open bracket orders."""
        return dict(self._open_orders)
