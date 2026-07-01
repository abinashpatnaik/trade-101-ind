"""
alpaca_connector.py
===================
Alpaca REST API connector for US Equities.

Handles authentication, order execution, position querying, and account summary
using the official alpaca-py SDK.

This is a self-contained, drop-in replacement for zerodha_connector.py.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, StopOrderRequest, TrailingStopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

from config import config

logger = logging.getLogger(__name__)


class AlpacaConnector:
    """
    High-level interface to the Alpaca API for US Equities.
    """

    def __init__(self) -> None:
        self.api_key = config.alpaca.api_key
        self.api_secret = config.alpaca.api_secret
        self.paper_mode = config.alpaca.paper_mode

        self.trading_client: Optional[TradingClient] = None
        self.data_client: Optional[StockHistoricalDataClient] = None
        self._authenticated = False

        if not self.api_key or not self.api_secret:
            logger.warning("AlpacaConnector: APCA_API_KEY_ID or APCA_API_SECRET_KEY not set.")

    # ------------------------------------------------------------------
    # Authentication & Session Management
    # ------------------------------------------------------------------

    def wait_for_gateway(self, timeout_seconds: int = 120) -> bool:
        try:
            self.connect()
            return self._authenticated
        except Exception as exc:
            logger.error("wait_for_gateway failed: %s", exc)
            return False

    def is_authenticated(self) -> bool:
        return self._authenticated

    def is_connected(self) -> bool:
        return self.is_authenticated()

    def connect(self) -> None:
        """Authenticate with Alpaca."""
        if not self.api_key or not self.api_secret:
            raise ConnectionError("Alpaca credentials missing.")

        try:
            self.trading_client = TradingClient(self.api_key, self.api_secret, paper=self.paper_mode)
            self.data_client = StockHistoricalDataClient(self.api_key, self.api_secret)
            
            # Test connection by fetching account
            account = self.trading_client.get_account()
            if account.account_blocked:
                raise ConnectionError("Alpaca account is blocked.")
                
            self._authenticated = True
            logger.info("Successfully connected to Alpaca (Paper=%s).", self.paper_mode)
        except Exception as exc:
            self._authenticated = False
            logger.error("Alpaca connection failed: %s", exc)
            raise ConnectionError(f"Alpaca connection failed: {exc}")

    def disconnect(self) -> None:
        logger.info("AlpacaConnector: disconnect() called — no-op.")

    def keepalive(self) -> None:
        if not self.is_authenticated():
            self.connect()

    # ------------------------------------------------------------------
    # Account & Portfolio API
    # ------------------------------------------------------------------

    def get_account_summary(self) -> Dict[str, float]:
        if not self.trading_client:
            return {}
        try:
            account = self.trading_client.get_account()
            
            net_liquidation = float(account.portfolio_value)
            available_funds = float(account.cash)
            daily_pnl = float(account.equity) - float(account.last_equity) if account.last_equity else 0.0

            result = {
                "NetLiquidation": net_liquidation,
                "AvailableFunds": available_funds,
                "DailyPnL": daily_pnl,
            }
            logger.debug("Alpaca Account summary: %s", result)
            return result
        except Exception as exc:
            logger.error("Failed to get Alpaca account summary: %s", exc)
            return {"NetLiquidation": 0.0, "AvailableFunds": 0.0, "DailyPnL": 0.0}

    def get_positions(self) -> Dict[str, Dict]:
        if not self.trading_client:
            return {}
        try:
            alpaca_positions = self.trading_client.get_all_positions()
            positions = {}
            for pos in alpaca_positions:
                qty = float(pos.qty)
                if qty == 0:
                    continue
                symbol = pos.symbol
                positions[symbol] = {
                    "quantity": qty,
                    "avg_cost": float(pos.avg_entry_price),
                    "market_value": float(pos.market_value),
                    "conid": 0,
                }
            logger.debug("Alpaca positions synced: %d active.", len(positions))
            return positions
        except Exception as exc:
            logger.error("Failed to get Alpaca positions: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    def get_current_price(self, symbol: str) -> Optional[float]:
        if not self.data_client:
            return None
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=[symbol])
            quotes = self.data_client.get_stock_latest_quote(req)
            if symbol in quotes:
                return float(quotes[symbol].ask_price)
        except Exception as exc:
            logger.error("Failed to get current price for %s: %s", symbol, exc)
        return None

    def get_conid(self, symbol: str) -> Optional[int]:
        return 0

    # ------------------------------------------------------------------
    # Order Execution API
    # ------------------------------------------------------------------

    def place_market_order(self, symbol: str, action: str, quantity: float, **kwargs) -> Optional[str]:
        if not self.trading_client:
            return None
            
        if action.upper() == "SELL" and not config.risk.allow_short_selling:
            positions = self.get_positions()
            held_qty = float(positions.get(symbol, {}).get("quantity", 0))
            if held_qty <= 0:
                logger.warning("Short selling blocked for %s.", symbol)
                return None
            if quantity > held_qty:
                quantity = held_qty

        try:
            side = OrderSide.BUY if action.upper() == "BUY" else OrderSide.SELL
            req = MarketOrderRequest(
                symbol=symbol,
                qty=round(quantity, 4),
                side=side,
                time_in_force=TimeInForce.DAY
            )
            order = self.trading_client.submit_order(order_data=req)
            logger.info("Alpaca MARKET order placed: %s %.4f %s, ID: %s", action, quantity, symbol, order.id)
            return str(order.id)
        except Exception as exc:
            logger.error("Failed to place MARKET order for %s: %s", symbol, exc)
            return None

    def place_stop_order(self, symbol: str, action: str, quantity: int, stop_price: float) -> Optional[str]:
        if not self.trading_client:
            return None
        try:
            side = OrderSide.BUY if action.upper() == "BUY" else OrderSide.SELL
            req = StopOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
                stop_price=round(stop_price, 2)
            )
            order = self.trading_client.submit_order(order_data=req)
            return str(order.id)
        except Exception as exc:
            logger.error("Failed to place stop order for %s: %s", symbol, exc)
            return None

    def place_stop_loss(self, symbol: str, action: str, quantity: int, stop_price: float) -> Optional[str]:
        return self.place_stop_order(symbol, action, quantity, stop_price)

    def place_trailing_stop_order(self, symbol: str, action: str, quantity: float, trail_percent: float) -> Optional[str]:
        if not self.trading_client:
            return None
        try:
            side = OrderSide.BUY if action.upper() == "BUY" else OrderSide.SELL
            req = TrailingStopOrderRequest(
                symbol=symbol,
                qty=round(quantity, 4),
                side=side,
                time_in_force=TimeInForce.DAY,
                trail_percent=round(trail_percent * 100, 2)  # Alpaca expects percentage as e.g. 1.5
            )
            order = self.trading_client.submit_order(order_data=req)
            logger.info("Alpaca TRAILING STOP order placed: %s %.4f %s, ID: %s, Trail: %.2f%%", action, quantity, symbol, order.id, trail_percent * 100)
            return str(order.id)
        except Exception as exc:
            logger.error("Failed to place trailing stop order for %s: %s", symbol, exc)
            return None

    def place_limit_order(self, symbol: str, action: str, quantity: int, limit_price: float) -> Optional[str]:
        if not self.trading_client:
            return None
        try:
            side = OrderSide.BUY if action.upper() == "BUY" else OrderSide.SELL
            req = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price, 2)
            )
            order = self.trading_client.submit_order(order_data=req)
            return str(order.id)
        except Exception as exc:
            logger.error("Failed to place limit order for %s: %s", symbol, exc)
            return None

    def cancel_order(self, order_id: str) -> bool:
        if not self.trading_client:
            return False
        try:
            self.trading_client.cancel_order_by_id(order_id)
            return True
        except Exception as exc:
            logger.error("Failed to cancel Alpaca order %s: %s", order_id, exc)
            return False

    def get_open_orders(self) -> List[Dict]:
        if not self.trading_client:
            return []
        try:
            orders = self.trading_client.get_orders()
            return [o.dict() for o in orders]
        except Exception as exc:
            logger.error("Failed to get open orders from Alpaca: %s", exc)
            return []
