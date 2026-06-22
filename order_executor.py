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

        # --- Stop-loss (STP) ---
        try:
            sl_order_id = self._ibkr.place_stop_loss(
                symbol=symbol,
                action="SELL",
                quantity=quantity,
                stop_price=stop_loss_price,
            )
            logger.info(
                "Stop-loss STP placed for %s @ %.4f (order_id=%s)",
                symbol, stop_loss_price, sl_order_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to place stop-loss for %s: %s", symbol, exc, exc_info=True
            )

        # --- Take-profit (LMT) ---
        try:
            tp_order_id = self._ibkr.place_limit_order(
                symbol=symbol,
                action="SELL",
                quantity=quantity,
                limit_price=take_profit_price,
            )
            logger.info(
                "Take-profit LMT placed for %s @ %.4f (order_id=%s)",
                symbol, take_profit_price, tp_order_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to place take-profit for %s: %s", symbol, exc, exc_info=True
            )

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

    def update_trailing_stops(
        self,
        symbol: str,
        current_price: float,
    ) -> bool:
        """
        Update the trailing high-water mark for *symbol* and trigger a market
        SELL if the current price drops more than ``_trailing_stop_pct`` from
        the high.

        Parameters
        ----------
        symbol:
            Bare LSE ticker.
        current_price:
            Latest market price.

        Returns
        -------
        bool
            True if the trailing stop fired (position was closed), False
            otherwise.
        """
        order = self._open_orders.get(symbol)
        if order is None:
            return False

        if current_price <= 0:
            return False

        # Advance the high-water mark.
        if symbol not in self._trailing_high:
            self._trailing_high[symbol] = current_price
        if current_price > self._trailing_high[symbol]:
            self._trailing_high[symbol] = current_price
            logger.debug(
                "Trailing high updated for %s: %.4f", symbol, current_price
            )

        trailing_stop_trigger = self._trailing_high[symbol] * (
            1.0 - self._trailing_stop_pct
        )

        if current_price <= trailing_stop_trigger:
            logger.warning(
                "TRAILING STOP triggered for %s: price=%.4f <= trigger=%.4f "
                "(high=%.4f, pct=%.1f%%)",
                symbol,
                current_price,
                trailing_stop_trigger,
                self._trailing_high[symbol],
                self._trailing_stop_pct * 100,
            )
            # Cancel the existing stop-loss bracket order if present.
            if order.stop_loss_order_id is not None:
                try:
                    self._ibkr.cancel_order(order.stop_loss_order_id)
                    logger.info(
                        "Cancelled stop-loss order %s for %s (trailing stop taking over)",
                        order.stop_loss_order_id, symbol,
                    )
                except Exception as exc:
                    logger.warning(
                        "Could not cancel stop-loss order %s for %s: %s",
                        order.stop_loss_order_id, symbol, exc,
                    )

            # Place a market SELL to close the position immediately.
            try:
                self._ibkr.place_market_order(
                    symbol=symbol,
                    action="SELL",
                    quantity=order.quantity,
                )
            except Exception as exc:
                logger.error(
                    "Failed to place trailing-stop SELL for %s: %s",
                    symbol, exc, exc_info=True,
                )

            # Clean up tracking state regardless of order placement outcome.
            self._open_orders.pop(symbol, None)
            self._trailing_high.pop(symbol, None)
            return True

        return False

    def check_exit_conditions(
        self,
        symbol: str,
        current_price: float,
        position: Dict,
    ) -> Optional[str]:
        """
        Software-level exit check as a fallback for cases where IBKR bracket
        orders are not triggered (e.g. in paper trading, fast gaps, or if the
        exchange STP/LMT orders were rejected).

        Checks (in order):
          1. Trailing stop  — tightest guard; fires first.
          2. Fixed stop-loss — 2% below entry.
          3. Take-profit    — 4% above entry.

        The caller is responsible for acting on the returned signal by placing
        a SELL market order (except for TRAILING_STOP, which places its own
        market order internally).

        Parameters
        ----------
        symbol:
            Bare LSE ticker.
        current_price:
            Latest market price.
        position:
            Dict with at minimum ``{'quantity': int, 'avg_cost': float}``.

        Returns
        -------
        str or None
            ``'TRAILING_STOP'`` — trailing stop fired (order already placed).
            ``'STOP_LOSS'``     — fixed stop-loss level hit.
            ``'TAKE_PROFIT'``   — take-profit level hit.
            ``None``            — no exit condition triggered.
        """
        order = self._open_orders.get(symbol)
        if order is None:
            # No tracked bracket entry for this symbol — cannot evaluate.
            return None

        if current_price <= 0:
            return None

        # --- 1. Trailing stop (checked first — tighter than fixed SL) ---
        if self.update_trailing_stops(symbol, current_price):
            return "TRAILING_STOP"

        # After a trailing stop fires _open_orders[symbol] is removed, so we
        # must re-fetch to guard the remainder of this method.
        order = self._open_orders.get(symbol)
        if order is None:
            return None

        # --- 2. Fixed stop-loss ---
        if order.stop_loss_price > 0 and current_price <= order.stop_loss_price:
            logger.warning(
                "STOP_LOSS hit for %s: price=%.4f <= sl=%.4f",
                symbol, current_price, order.stop_loss_price,
            )
            self._open_orders.pop(symbol, None)
            return "STOP_LOSS"

        # --- 3. Take-profit ---
        if order.take_profit_price > 0 and current_price >= order.take_profit_price:
            logger.info(
                "TAKE_PROFIT hit for %s: price=%.4f >= tp=%.4f",
                symbol, current_price, order.take_profit_price,
            )
            self._open_orders.pop(symbol, None)
            return "TAKE_PROFIT"

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
