"""
zerodha_connector.py
====================
Zerodha Kite Connect API connector.

Handles authentication (automated username/password + TOTP 2FA via pyotp),
access token caching, order execution, position querying, and account summary.

This is a self-contained, drop-in replacement for ibkr_connector.py.
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple
import requests
import pyotp
import threading
from kiteconnect import KiteConnect, KiteTicker, exceptions

logger = logging.getLogger(__name__)

class ZerodhaConnector:
    """
    High-level interface to the Zerodha Kite Connect API.
    
    Manages automated login using pyotp, caches access tokens locally,
    and exposes the required methods for portfolio tracking and order execution.
    """

    def __init__(self) -> None:
        # Load settings from environment variables with sensible fallbacks
        self.api_key = os.getenv("KITE_API_KEY", "").strip()
        self.api_secret = os.getenv("KITE_API_SECRET", "").strip()
        self.user_id = os.getenv("KITE_USER_ID", "").strip()
        self.password = os.getenv("KITE_PASSWORD", "").strip()
        self.totp_secret = os.getenv("KITE_TOTP_SECRET", "").strip()
        
        # Token cache file
        in_docker = os.path.exists("/app")
        self.token_file = "/app/data/kite_access_token.txt" if in_docker else "trading_agent/kite_access_token.txt"
        
        self.kite: Optional[KiteConnect] = None
        self._authenticated = False
        
        # WebSocket / Ticker state
        self.kws: Optional[KiteTicker] = None
        self._kws_thread: Optional[threading.Thread] = None
        self._live_prices: Dict[str, float] = {}
        self._subscribed_tokens: set = set()
        self._token_to_symbol: Dict[int, str] = {}

        if not self.api_key or not self.api_secret:
            logger.warning("ZerodhaConnector: KITE_API_KEY or KITE_API_SECRET not set.")
        if not self.user_id or not self.password or not self.totp_secret:
            logger.warning("ZerodhaConnector: Kite login credentials (user_id/password/totp_secret) not fully set. Automated login will fail.")

        # Initialize Kite client
        if self.api_key:
            self.kite = KiteConnect(api_key=self.api_key)

    # ------------------------------------------------------------------
    # Authentication & Session Management
    # ------------------------------------------------------------------

    def wait_for_gateway(self, timeout_seconds: int = 120) -> bool:
        """Compatibility shim for main agent. Calls connect()."""
        try:
            self.connect()
            return self._authenticated
        except Exception as exc:
            logger.error("wait_for_gateway failed: %s", exc)
            return False

    def is_authenticated(self) -> bool:
        """Check if client is authenticated by calling profile endpoint."""
        if not self.kite or not self._authenticated:
            return False
        try:
            self.kite.profile()
            return True
        except exceptions.TokenException:
            logger.warning("Kite access token has expired or is invalid.")
            self._authenticated = False
            return False
        except Exception as exc:
            logger.error("Kite profile check failed: %s", exc)
            return False

    def is_connected(self) -> bool:
        """Alias for compatibility with agent checks."""
        return self.is_authenticated()

    def connect(self) -> None:
        """
        Authenticate with Zerodha.
        First attempts to reuse a cached token. If invalid, performs automated login.
        """
        if not self.kite:
            raise ConnectionError("Kite client not initialized. Check API Key.")

        # Step 1: Try to reuse cached token
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, "r") as f:
                    cached_token = f.read().strip()
                if cached_token:
                    logger.info("Attempting to reuse cached Zerodha access token...")
                    self.kite.set_access_token(cached_token)
                    # Verify token validity
                    self.kite.profile()
                    self._authenticated = True
                    logger.info("Successfully authenticated with cached access token.")
                    self._init_websocket()
                    return
            except exceptions.TokenException:
                logger.info("Cached access token is invalid/expired. Proceeding to login.")
            except Exception as exc:
                logger.warning("Failed to verify cached token: %s", exc)

        # Step 2: Automated Login Sequence
        logger.info("Performing automated login to Zerodha Kite...")
        try:
            # 1. Initialize session and cookies
            session = requests.Session()
            login_url = self.kite.login_url()
            session.get(login_url)

            # 2. POST Username & Password
            login_payload = {"user_id": self.user_id, "password": self.password}
            login_resp = session.post("https://kite.zerodha.com/api/login", data=login_payload)
            login_resp.raise_for_status()
            
            login_data = login_resp.json()
            if login_data.get("status") != "success":
                raise ConnectionError(f"Zerodha login failed: {login_data.get('message')}")
                
            request_id = login_data["data"]["request_id"]

            # 3. Generate TOTP and POST 2FA
            totp = pyotp.TOTP(self.totp_secret).now()
            twofa_payload = {
                "user_id": self.user_id,
                "request_id": request_id,
                "twofa_value": totp,
                "twofa_type": "totp"
            }
            twofa_resp = session.post("https://kite.zerodha.com/api/twofa", data=twofa_payload)
            twofa_resp.raise_for_status()
            
            twofa_data = twofa_resp.json()
            if twofa_data.get("status") != "success":
                raise ConnectionError(f"Zerodha 2FA failed: {twofa_data.get('message')}")

            # 4. Visit login URL again with skip_session=true to force the auto-redirect
            login_url_with_skip = login_url + "&skip_session=true"
            try:
                redirect_resp = session.get(login_url_with_skip, allow_redirects=True)
                redirect_url = redirect_resp.url
            except requests.exceptions.ConnectionError as e:
                # The redirect worked, but requests failed to connect to the dummy 127.0.0.1 URL.
                # We can safely extract the final destination URL from the exception's request object.
                if e.request:
                    redirect_url = e.request.url
                else:
                    raise
            
            parsed_url = urllib.parse.urlparse(redirect_url)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            request_token = query_params.get("request_token", [None])[0]

            if not request_token:
                logger.error(f"Failed redirect URL: {redirect_url}")
                logger.error(f"Response text: {redirect_resp.text[:500]}")
                raise ConnectionError("Failed to retrieve request_token from final redirect.")

            # 5. Generate and Cache Access Token
            session_data = self.kite.generate_session(request_token, api_secret=self.api_secret)
            access_token = session_data["access_token"]
            
            # Save token to file
            os.makedirs(os.path.dirname(self.token_file) or ".", exist_ok=True)
            with open(self.token_file, "w") as f:
                f.write(access_token)
            
            self.kite.set_access_token(access_token)
            self._authenticated = True
            logger.info("Zerodha automated login successful. New access token cached.")
            
            self._init_websocket()
            
        except Exception as exc:
            self._authenticated = False
            logger.error("Zerodha automated login failed: %s", exc)
            raise ConnectionError(f"Zerodha authentication failed: {exc}")

    def disconnect(self) -> None:
        """No-op for REST connector."""
        logger.info("ZerodhaConnector: disconnect() called — no-op.")

    def keepalive(self) -> None:
        """Verify session is still valid. If invalid, try to reconnect."""
        if not self.is_authenticated():
            logger.warning("Session dead during keepalive check. Reconnecting...")
            try:
                self.connect()
            except Exception as exc:
                logger.error("Failed to reconnect during keepalive: %s", exc)

    # ------------------------------------------------------------------
    # WebSockets (Live Quotes)
    # ------------------------------------------------------------------

    def _init_websocket(self) -> None:
        """Initialize and start the KiteTicker WebSocket connection in a background thread."""
        if not self.kite or not self.kite.access_token:
            return
            
        if self.kws and self.kws.is_connected():
            return

        self.kws = KiteTicker(self.api_key, self.kite.access_token)
        
        def on_ticks(ws, ticks):
            for tick in ticks:
                token = tick.get("instrument_token")
                price = tick.get("last_price")
                if token and price and token in self._token_to_symbol:
                    symbol = self._token_to_symbol[token]
                    self._live_prices[symbol] = price
                    
                    # Trigger instant callback if defined (for instant trailing stop)
                    if hasattr(self, "on_price_update_callback") and callable(getattr(self, "on_price_update_callback")):
                        try:
                            self.on_price_update_callback(symbol, price)
                        except Exception as exc:
                            logger.error("Error in on_price_update_callback for %s: %s", symbol, exc)

        def on_connect(ws, response):
            logger.info("Kite WebSocket connected.")
            # Resubscribe to existing tokens if reconnected
            if self._subscribed_tokens:
                ws.subscribe(list(self._subscribed_tokens))
                ws.set_mode(ws.MODE_LTP, list(self._subscribed_tokens))

        def on_close(ws, code, reason):
            logger.warning(f"Kite WebSocket closed: {code} - {reason}")

        def on_error(ws, code, reason):
            logger.error(f"Kite WebSocket error: {code} - {reason}")

        self.kws.on_ticks = on_ticks
        self.kws.on_connect = on_connect
        self.kws.on_close = on_close
        self.kws.on_error = on_error

        # Let kiteconnect manage the background thread so it configures Twisted correctly
        # (Twisted crashes if you run reactor.run() in a user thread because it tries to install signal handlers)
        self.kws.connect(threaded=True, disable_ssl_verification=False)

    def subscribe(self, symbols: List[str]) -> None:
        """
        Subscribe to live ticker updates for the given list of symbols.
        Resolves instrument tokens via the REST LTP endpoint if not already known.
        """
        if not self.kite or not self.kws:
            return
            
        new_instruments = []
        # Map symbol -> "NSE:SYMBOL" for lookup
        for sym in symbols:
            if sym not in self._live_prices:  # Unseen symbol
                tradingsymbol = sym.split(".")[0]
                new_instruments.append(f"NSE:{tradingsymbol}")
                
        if not new_instruments:
            return
            
        # Fetch tokens via REST API
        try:
            ltp_data = self.kite.ltp(new_instruments)
            tokens_to_subscribe = []
            
            for inst, data in ltp_data.items():
                token = data.get("instrument_token")
                tradingsymbol = inst.split(":")[1]
                sym = f"{tradingsymbol}.NS"
                
                if token:
                    self._token_to_symbol[token] = sym
                    self._subscribed_tokens.add(token)
                    tokens_to_subscribe.append(token)
                    
                    # Store the initial price so we have it immediately
                    if "last_price" in data:
                        self._live_prices[sym] = data["last_price"]
            
            # Subscribe over WebSocket
            if self.kws.is_connected() and tokens_to_subscribe:
                self.kws.subscribe(tokens_to_subscribe)
                self.kws.set_mode(self.kws.MODE_LTP, tokens_to_subscribe)
                logger.info(f"Subscribed to WebSockets for {len(tokens_to_subscribe)} new symbols.")
                
        except Exception as exc:
            logger.error("Failed to subscribe symbols: %s", exc)

    # ------------------------------------------------------------------
    # Account & Portfolio API
    # ------------------------------------------------------------------

    def get_account_summary(self) -> Dict[str, float]:
        """
        Fetch account balance and calculate P&L.
        Returns:
            dict with keys: NetLiquidation, AvailableFunds, DailyPnL.
        """
        if not self.kite:
            return {}
        try:
            margins = self.kite.margins()
            equity = margins.get("equity", {})
            available_funds = float(equity.get("net", 0.0))  # Net available margin
            
            # Retrieve positions to calculate market value and P&L
            pos_data = self.kite.positions()
            net_positions = pos_data.get("net", [])
            
            daily_pnl = 0.0
            open_positions_value = 0.0
            
            for pos in net_positions:
                qty = int(pos.get("quantity", 0))
                last_price = float(pos.get("last_price", 0.0))
                pnl = float(pos.get("pnl", 0.0))
                daily_pnl += pnl
                
                if qty > 0:
                    open_positions_value += qty * last_price
            
            net_liquidation = available_funds + open_positions_value
            
            result = {
                "NetLiquidation": net_liquidation,
                "AvailableFunds": available_funds,
                "DailyPnL": daily_pnl,
            }
            logger.debug("Zerodha Account summary: %s", result)
            return result
        except Exception as exc:
            err_msg = str(exc)
            if "UNKNOWN_REQUEST" in err_msg or "Message build error" in err_msg:
                # Zerodha nightly maintenance error, don't spam the logs
                logger.debug("Zerodha maintenance window (UNKNOWN_REQUEST/Message build error), skipping account summary sync.")
            else:
                logger.error("Failed to get account summary: %s", exc)
            return {}

    def get_positions(self) -> Optional[Dict[str, Dict]]:
        """
        Fetch open positions.
        Returns:
            dict mapping symbol (with .NS) -> {quantity, avg_cost, market_value, conid}
        """
        if not self.kite:
            return None
        try:
            pos_data = self.kite.positions()
            net_positions = pos_data.get("net", [])
            positions = {}
            for pos in net_positions:
                qty = int(pos.get("quantity", 0))
                if qty == 0:
                    continue
                tradingsymbol = pos.get("tradingsymbol", "")
                symbol = tradingsymbol + ".NS"
                
                last_price = float(pos.get("last_price", 0.0))
                positions[symbol] = {
                    "quantity": qty,
                    "avg_cost": float(pos.get("average_price", 0.0)),
                    "market_value": qty * last_price,
                    "conid": 0,  # Not used for Kite but kept for compatibility
                }
            logger.debug("Zerodha positions synced: %d active.", len(positions))
            return positions
        except Exception as exc:
            err_msg = str(exc)
            if "UNKNOWN_REQUEST" in err_msg:
                # Zerodha nightly maintenance error, don't spam the logs
                logger.debug("Zerodha maintenance window (UNKNOWN_REQUEST), skipping positions sync.")
            else:
                logger.error("Failed to fetch positions: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Retrieve live LTP for a symbol. First checks the WebSocket live price cache.
        If missing, triggers a REST request and auto-subscribes.
        """
        if symbol in self._live_prices and self._live_prices[symbol] > 0:
            return self._live_prices[symbol]
            
        if not self.kite:
            return None
        try:
            tradingsymbol = symbol.split(".")[0]
            instrument = f"NSE:{tradingsymbol}"
            ltp_data = self.kite.ltp(instrument)
            
            token = ltp_data.get(instrument, {}).get("instrument_token")
            price = float(ltp_data.get(instrument, {}).get("last_price", 0.0))
            
            if price > 0:
                self._live_prices[symbol] = price
                # Auto-subscribe for future tick updates
                if token and self.kws and self.kws.is_connected():
                    self._token_to_symbol[token] = symbol
                    self._subscribed_tokens.add(token)
                    self.kws.subscribe([token])
                    self.kws.set_mode(self.kws.MODE_LTP, [token])
                return price
        except Exception as exc:
            logger.error("Failed to get current price for %s: %s", symbol, exc)
        return None

    def get_conid(self, symbol: str) -> Optional[int]:
        """Compatibility shim. Returns 0 as contract IDs are not used."""
        return 0

    def get_historical_data(self, symbol: str, start_dt, end_dt, interval: str = "5minute") -> Optional[pd.DataFrame]:
        """
        Fetch historical data from Zerodha API.
        WARNING: This consumes API credits. Use sparingly (e.g. only for top scanned candidates).
        interval can be "minute", "day", "3minute", "5minute", "10minute", "15minute", "30minute", "60minute".
        """
        if not self.kite:
            return None
        try:
            tradingsymbol = symbol.split(".")[0]
            instrument = f"NSE:{tradingsymbol}"
            # Need instrument_token for historical data
            ltp_data = self.kite.ltp(instrument)
            token = ltp_data.get(instrument, {}).get("instrument_token")
            if not token:
                logger.error("Could not resolve token for %s historical data", symbol)
                return None
                
            import pandas as pd
            records = self.kite.historical_data(token, start_dt, end_dt, interval)
            if not records:
                return None
                
            df = pd.DataFrame(records)
            df.rename(columns={
                'date': 'Date',
                'open': 'Open',
                'high': 'High',
                'low': 'Low',
                'close': 'Close',
                'volume': 'Volume'
            }, inplace=True)
            df.set_index('Date', inplace=True)
            return df
        except Exception as exc:
            logger.error("Failed to fetch historical data from Zerodha for %s: %s", symbol, exc)
            return None

    # ------------------------------------------------------------------
    # Order Execution API
    # ------------------------------------------------------------------

    def place_market_order(self, symbol: str, action: str, quantity: int, **kwargs) -> Optional[str]:
        """Place a market order on NSE."""
        if not self.kite:
            return None
        
        # Guard: check position for short selling
        from config import config
        if action.upper() == "SELL" and not config.risk.allow_short_selling:
            positions = self.get_positions()
            held_qty = int(positions.get(symbol, {}).get("quantity", 0))
            if held_qty <= 0:
                logger.warning("Short selling blocked for %s.", symbol)
                return None
            if quantity > held_qty:
                logger.warning("Capping SELL quantity for %s to held amount: %d", symbol, held_qty)
                quantity = held_qty

        try:
            tradingsymbol = symbol.split(".")[0]
            order_id = self.kite.place_order(
                variety="regular",
                exchange="NSE",
                tradingsymbol=tradingsymbol,
                transaction_type=action.upper(),
                quantity=quantity,
                product="CNC",
                order_type="MARKET"
            )
            logger.info("Kite MARKET order placed: %s %d %s, ID: %s", action, quantity, symbol, order_id)
            return str(order_id)
        except Exception as exc:
            logger.error("Failed to place MARKET order for %s: %s", symbol, exc)
            return None

    def place_stop_order(self, symbol: str, action: str, quantity: int, stop_price: float) -> Optional[str]:
        """
        Place a stop-loss market order (SL-M) on NSE.
        Fires a market order when price reaches the trigger price.
        """
        if not self.kite:
            return None
        try:
            tradingsymbol = symbol.split(".")[0]
            # Use SL-M (Stop Loss Market) order type for simplicity and guaranteed execution
            order_id = self.kite.place_order(
                variety="regular",
                exchange="NSE",
                tradingsymbol=tradingsymbol,
                transaction_type=action.upper(),
                quantity=quantity,
                product="CNC",
                order_type="SL-M",
                trigger_price=round(stop_price, 2)
            )
            logger.info("Kite SL-M order placed: %s %d %s stop=%.2f, ID: %s", action, quantity, symbol, stop_price, order_id)
            return str(order_id)
        except Exception as exc:
            logger.error("Failed to place stop order for %s: %s", symbol, exc)
            return None

    def place_stop_loss(self, symbol: str, action: str, quantity: int, stop_price: float) -> Optional[str]:
        """Compatibility alias for place_stop_order to address executor discrepancies."""
        return self.place_stop_order(symbol, action, quantity, stop_price)

    def place_limit_order(self, symbol: str, action: str, quantity: int, limit_price: float) -> Optional[str]:
        """Place a limit order (LMT) on NSE."""
        if not self.kite:
            return None
        try:
            tradingsymbol = symbol.split(".")[0]
            order_id = self.kite.place_order(
                variety="regular",
                exchange="NSE",
                tradingsymbol=tradingsymbol,
                transaction_type=action.upper(),
                quantity=quantity,
                product="CNC",
                order_type="LIMIT",
                price=round(limit_price, 2)
            )
            logger.info("Kite LIMIT order placed: %s %d %s limit=%.2f, ID: %s", action, quantity, symbol, limit_price, order_id)
            return str(order_id)
        except Exception as exc:
            logger.error("Failed to place limit order for %s: %s", symbol, exc)
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        if not self.kite:
            return False
        try:
            self.kite.cancel_order(variety="regular", order_id=order_id)
            logger.info("Kite order %s cancelled successfully.", order_id)
            return True
        except Exception as exc:
            logger.error("Failed to cancel Kite order %s: %s", order_id, exc)
            return False

    def get_open_orders(self) -> List[Dict]:
        """Fetch open orders from Kite Connect."""
        if not self.kite:
            return []
        try:
            orders = self.kite.orders()
            # Filter for open or pending trigger statuses
            open_orders = [o for o in orders if o.get("status") in ("OPEN", "TRIGGER PENDING", "VALIDATION PENDING", "PUT ORDER REQ RECEIVED")]
            return open_orders
        except Exception as exc:
            logger.error("Failed to get open orders from Kite: %s", exc)
            return []
