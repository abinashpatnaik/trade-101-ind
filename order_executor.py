"""
order_executor.py
=================
Translates Decision objects into live broker orders.

For each BUY decision the executor:
  1. Places a market order for the desired quantity (fractional OK).
  2. If quantity >= 1 (whole shares): places a native Alpaca Trailing Stop.
  3. If quantity < 1 (fractional): uses software-level trailing stop polling.

For each SELL decision the executor places a market order to close the
full position.

Requires:
    zerodha_connector / alpaca_connector
"""

from __future__ import annotations

import logging
import time
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
    quantity: float
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    stop_loss_order_id: Optional[int] = None
    take_profit_order_id: Optional[int] = None
    is_fractional: bool = False               # True = software trailing stop


class OrderExecutor:
    """
    Executes trading decisions via broker connector.

    Supports a hybrid trailing stop strategy:
    - Whole shares (qty >= 1): Native Alpaca trailing stop (instant execution)
    - Fractional shares (qty < 1): Software trailing stop (polled each loop)
    """

    _trailing_stop_pct: float = config.risk.trailing_stop_pct

    def __init__(self, ibkr: IBKRConnector) -> None:
        self._ibkr = ibkr
        self._open_orders: Dict[str, OpenOrder] = {}
        self._trailing_high: Dict[str, float] = {}
        logger.debug("OrderExecutor initialised (trailing_stop=%.2f%%).", self._trailing_stop_pct * 100)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_fractional(self, quantity: float) -> bool:
        """Return True if quantity is less than 1 whole share."""
        return quantity < 1.0

    def _place_bracket(
        self,
        symbol: str,
        quantity: float,
        entry_order_id: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> OpenOrder:
        """
        Submit trailing stop after market-buy.

        - Whole shares: native Alpaca trailing stop order.
        - Fractional shares: skip native order, use software trailing stop.
        """
        sl_order_id: Optional[int] = None
        tp_order_id: Optional[int] = None
        fractional = self._is_fractional(quantity)

        if fractional:
            logger.info(
                "Fractional position for %s (qty=%.4f) — using SOFTWARE trailing stop.",
                symbol, quantity,
            )
        else:
            # --- Native Trailing Stop (whole shares only) ---
            for attempt in range(5):
                try:
                    sl_order_id = self._ibkr.place_trailing_stop_order(
                        symbol=symbol,
                        action="SELL",
                        quantity=quantity,
                        trail_percent=config.risk.trailing_stop_pct,
                    )
                    logger.info(
                        "Native Trailing Stop placed for %s @ %.2f%% trail (order_id=%s)",
                        symbol, config.risk.trailing_stop_pct * 100, sl_order_id,
                    )
                    break
                except Exception as exc:
                    if attempt < 4:
                        logger.warning(
                            "Trailing stop rejected for %s (waiting for BUY fill). Retrying in 2s...",
                            symbol,
                        )
                        time.sleep(2)
                    else:
                        logger.error(
                            "Failed to place trailing stop for %s after 5 attempts: %s",
                            symbol, exc, exc_info=True,
                        )
                        # Fall back to software trailing stop
                        fractional = True
                        logger.info("Falling back to SOFTWARE trailing stop for %s.", symbol)

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
            is_fractional=fractional,
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

        Returns True if the primary order was placed successfully.
        """
        if decision.action == "HOLD":
            logger.debug("OrderExecutor.execute(): HOLD for %s — no action.", symbol)
            return True

        if not self._ibkr.is_connected():
            logger.error(
                "OrderExecutor.execute(): broker not connected — cannot execute "
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
            self._trailing_high[symbol] = current_price

            stop_type = "SOFTWARE" if open_order.is_fractional else "NATIVE"
            logger.info(
                "BUY executed for %s: qty=%.4f entry_id=%s sl=%.4f tp=%.4f stop=%s",
                symbol,
                decision.quantity,
                entry_order_id,
                decision.stop_loss_price,
                decision.take_profit_price,
                stop_type,
            )
            return True

        # ---------------------------------------------------------------
        # SELL
        # ---------------------------------------------------------------
        if decision.action == "SELL":
            live_positions = self._ibkr.get_positions()
            live_qty = float(live_positions.get(symbol, {}).get("quantity", 0))
            if live_qty <= 0:
                logger.warning(
                    "SELL skipped for %s — broker shows 0 shares held. Clearing.",
                    symbol,
                )
                self._open_orders.pop(symbol, None)
                self._trailing_high.pop(symbol, None)
                return False

            self._cancel_bracket(symbol)

            try:
                sell_order_id = self._ibkr.place_market_order(
                    symbol=symbol,
                    action="SELL",
                    quantity=live_qty,
                )
            except Exception as exc:
                logger.error(
                    "Failed to place SELL market order for %s: %s",
                    symbol, exc, exc_info=True,
                )
                return False

            self._open_orders.pop(symbol, None)
            self._trailing_high.pop(symbol, None)

            logger.info(
                "SELL executed for %s: qty=%.4f order_id=%s",
                symbol, live_qty, sell_order_id,
            )
            return True

        logger.warning(
            "OrderExecutor.execute(): unrecognised action '%s' for %s.",
            decision.action, symbol,
        )
        return False

    # ------------------------------------------------------------------
    # Software Trailing Stop (for fractional positions)
    # ------------------------------------------------------------------

    def check_exit_conditions(
        self,
        symbol: str,
        current_price: float,
        position: Dict,
    ) -> Optional[str]:
        """
        Software-level trailing stop for fractional positions.

        For whole-share positions with native Alpaca trailing stops, returns None
        (the broker handles the exit).

        For fractional positions, tracks the high-water mark and triggers a SELL
        if price drops more than trailing_stop_pct from the peak.

        Returns
        -------
        str or None
            ``'TRAILING_STOP'`` — software trailing stop fired.
            ``None``            — no exit condition triggered.
        """
        order = self._open_orders.get(symbol)
        if order is None:
            return None

        # Whole-share positions use native Alpaca stops — skip software check
        if not order.is_fractional:
            return None

        if current_price <= 0:
            return None

        # Advance the high-water mark
        if symbol not in self._trailing_high:
            self._trailing_high[symbol] = current_price
        if current_price > self._trailing_high[symbol]:
            self._trailing_high[symbol] = current_price
            logger.debug(
                "Software trailing high updated for %s: %.4f", symbol, current_price
            )

        trailing_stop_trigger = self._trailing_high[symbol] * (1.0 - self._trailing_stop_pct)

        if current_price <= trailing_stop_trigger:
            logger.warning(
                "SOFTWARE TRAILING STOP triggered for %s: price=%.4f <= trigger=%.4f "
                "(high=%.4f, pct=%.1f%%)",
                symbol,
                current_price,
                trailing_stop_trigger,
                self._trailing_high[symbol],
                self._trailing_stop_pct * 100,
            )
            self._open_orders.pop(symbol, None)
            self._trailing_high.pop(symbol, None)
            return "TRAILING_STOP"

        return None

    def close_position(self, symbol: str, quantity: float) -> bool:
        """
        Convenience method to immediately close an open position via a market
        SELL order without consulting the Decision engine.

        Cancels any open bracket orders for *symbol* first.

        Returns True if the order was successfully placed.
        """
        self._cancel_bracket(symbol)

        if not self._ibkr.is_connected():
            logger.error(
                "close_position(): broker not connected — cannot close %s.", symbol
            )
            return False

        try:
            order_id = self._ibkr.place_market_order(
                symbol=symbol,
                action="SELL",
                quantity=quantity,
            )
            self._open_orders.pop(symbol, None)
            self._trailing_high.pop(symbol, None)
            logger.info(
                "Position closed for %s: qty=%.4f order_id=%s",
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

