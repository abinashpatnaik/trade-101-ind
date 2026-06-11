"""
ibkr_connector.py
=================
IBKR Client Portal REST API connector.

Communicates with the CP Gateway process running at https://localhost:5000
(managed by IBeam for automated daily re-authentication).

All endpoints follow the IBKR Web API v1 spec:
  https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/

The gateway must be running and authenticated before any method is called.
Use IBKRConnector.wait_for_gateway() on startup to block until ready.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional

import pandas as pd
import requests
import urllib3

# Suppress InsecureRequestWarning for self-signed CP Gateway certificate.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------
_GATEWAY_URL: str = "https://localhost:5000"


class IBKRConnector:
    """
    High-level interface to the IBKR Client Portal REST API.

    Communicates with the CP Gateway (IBeam-managed) running at
    https://localhost:5000. All HTTP calls are thread-safe and never raise
    exceptions to callers — failures return safe defaults (None, {}, False).

    Usage
    -----
    >>> conn = IBKRConnector()
    >>> if conn.wait_for_gateway():
    ...     summary = conn.get_account_summary()
    ...     print(summary)
    """

    BASE_URL: str = "https://localhost:5000/v1/api"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.verify = False  # self-signed cert
        self._session.headers.update({"Content-Type": "application/json"})
        self._account_id: Optional[str] = None
        self._conid_cache: Dict[str, int] = {}  # symbol -> conid
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """
        Perform a GET request against the CP Gateway.

        Returns the parsed JSON body on success (2xx), or None on any error.
        Thread-safe via self._lock.
        """
        url = f"{self.BASE_URL}{path}"
        try:
            with self._lock:
                resp = self._session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            logger.error("HTTP error GET %s: %s", path, exc)
        except requests.exceptions.ConnectionError as exc:
            logger.error("Connection error GET %s: %s", path, exc)
        except requests.exceptions.Timeout:
            logger.error("Timeout GET %s", path)
        except Exception as exc:
            logger.error("Unexpected error GET %s: %s", path, exc, exc_info=True)
        return None

    def _post(self, path: str, body: Optional[Dict] = None) -> Optional[object]:
        """
        Perform a POST request against the CP Gateway.

        Returns the parsed JSON body on success (2xx), or None on any error.
        Response may be a dict or a list (order endpoints return lists).
        Thread-safe via self._lock.
        """
        url = f"{self.BASE_URL}{path}"
        try:
            with self._lock:
                resp = self._session.post(url, json=body or {}, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            logger.error("HTTP error POST %s: %s", path, exc)
        except requests.exceptions.ConnectionError as exc:
            logger.error("Connection error POST %s: %s", path, exc)
        except requests.exceptions.Timeout:
            logger.error("Timeout POST %s", path)
        except Exception as exc:
            logger.error("Unexpected error POST %s: %s", path, exc, exc_info=True)
        return None

    def _delete(self, path: str) -> Optional[Dict]:
        """
        Perform a DELETE request against the CP Gateway.

        Returns the parsed JSON body on success (2xx), or None on any error.
        Thread-safe via self._lock.
        """
        url = f"{self.BASE_URL}{path}"
        try:
            with self._lock:
                resp = self._session.delete(url, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            logger.error("HTTP error DELETE %s: %s", path, exc)
        except requests.exceptions.ConnectionError as exc:
            logger.error("Connection error DELETE %s: %s", path, exc)
        except requests.exceptions.Timeout:
            logger.error("Timeout DELETE %s", path)
        except Exception as exc:
            logger.error("Unexpected error DELETE %s: %s", path, exc, exc_info=True)
        return None

    # ------------------------------------------------------------------
    # Gateway readiness
    # ------------------------------------------------------------------

    def wait_for_gateway(self, timeout_seconds: int = 120) -> bool:
        """
        Block until the CP Gateway is authenticated or timeout is reached.

        Polls ``GET /iserver/auth/status`` every 5 seconds.
        Logs progress every 15 seconds.

        Parameters
        ----------
        timeout_seconds:
            Maximum seconds to wait before returning False.

        Returns
        -------
        bool
            True if authenticated before timeout, False otherwise.
        """
        logger.info(
            "Waiting for CP Gateway authentication (timeout=%ds) …",
            timeout_seconds,
        )
        deadline = time.monotonic() + timeout_seconds
        last_log = time.monotonic()
        attempts = 0

        while time.monotonic() < deadline:
            attempts += 1
            try:
                with self._lock:
                    resp = self._session.get(
                        f"{self.BASE_URL}/iserver/auth/status",
                        timeout=10,
                    )
                data = resp.json() if resp.ok else {}
                authenticated = data.get("authenticated", False)
                if authenticated:
                    logger.info(
                        "CP Gateway authenticated after %d attempt(s).", attempts
                    )
                    return True
            except Exception as exc:
                logger.debug("Gateway poll attempt %d failed: %s", attempts, exc)

            now = time.monotonic()
            if now - last_log >= 15:
                elapsed = int(now - (deadline - timeout_seconds))
                logger.info(
                    "Still waiting for CP Gateway … (%ds elapsed, %ds remaining)",
                    elapsed,
                    int(deadline - now),
                )
                last_log = now

            time.sleep(5)

        logger.error(
            "CP Gateway not authenticated after %d seconds.", timeout_seconds
        )
        return False

    def is_authenticated(self) -> bool:
        """
        Check current CP Gateway authentication status.

        Returns
        -------
        bool
            True if authenticated, False otherwise (including on errors).
        """
        data = self._get("/iserver/auth/status")
        if data is None:
            return False
        return bool(data.get("authenticated", False))

    def reauthenticate(self) -> bool:
        """
        Trigger a re-authentication via the CP Gateway.

        Returns True if the request was accepted, False otherwise.
        """
        result = self._post("/iserver/reauthenticate")
        if result is None:
            return False
        logger.info("Re-authentication triggered: %s", result)
        return True

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def _get_account_id(self) -> Optional[str]:
        """
        Retrieve and cache the primary account ID.

        Returns
        -------
        str or None
            Account ID string such as ``'U12345678'``, or None on failure.
        """
        if self._account_id:
            return self._account_id

        data = self._get("/portfolio/accounts")
        if not data or not isinstance(data, list) or len(data) == 0:
            logger.error("Could not retrieve account list.")
            return None

        account_id: str = str(data[0].get("id", ""))
        if not account_id:
            logger.error("Account ID missing in response: %s", data[0])
            return None

        self._account_id = account_id
        logger.info("Account ID resolved: %s", account_id)
        return account_id

    def get_account_summary(self) -> Dict:
        """
        Retrieve key account-level metrics.

        Calls ``GET /portfolio/{accountId}/summary``.

        Returns
        -------
        dict
            Keys: ``net_liquidation``, ``available_funds``, ``daily_pnl``.
            All values are floats (GBP).  Returns empty dict on failure.
        """
        account_id = self._get_account_id()
        if not account_id:
            return {}

        data = self._get(f"/portfolio/{account_id}/summary")
        if not data or not isinstance(data, dict):
            logger.warning("Empty or invalid account summary response.")
            return {}

        def _extract(key: str) -> float:
            """Extract ``amount`` from a nested summary field."""
            field = data.get(key, {})
            if isinstance(field, dict):
                return float(field.get("amount", 0.0))
            return 0.0

        result = {
            "net_liquidation": _extract("netliquidation"),
            "available_funds": _extract("availablefunds"),
            "daily_pnl": _extract("dailypnl"),
        }
        logger.debug("Account summary: %s", result)
        return result

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(self) -> Dict[str, Dict]:
        """
        Fetch all open equity positions for the primary account.

        Calls ``GET /portfolio/{accountId}/positions/0``.

        Returns
        -------
        dict
            Mapping ``{symbol: {'quantity': int, 'avg_cost': float,
            'market_value': float, 'conid': int}}``.
            Returns empty dict on failure.
        """
        account_id = self._get_account_id()
        if not account_id:
            return {}

        data = self._get(f"/portfolio/{account_id}/positions/0")
        if not data or not isinstance(data, list):
            logger.debug("No positions or invalid response.")
            return {}

        positions: Dict[str, Dict] = {}
        for pos in data:
            symbol: str = pos.get("ticker", pos.get("contractDesc", "UNKNOWN"))
            # Strip exchange suffix if present (e.g. "HSBA-LSE" → "HSBA")
            if "-" in symbol:
                symbol = symbol.split("-")[0]
            try:
                positions[symbol] = {
                    "quantity": int(pos.get("position", 0)),
                    "avg_cost": float(pos.get("avgCost", 0.0)),
                    "market_value": float(pos.get("mktValue", 0.0)),
                    "conid": int(pos.get("conid", 0)),
                }
            except (ValueError, TypeError) as exc:
                logger.warning("Could not parse position for %s: %s", symbol, exc)

        logger.debug("Positions fetched: %d open.", len(positions))
        return positions

    # ------------------------------------------------------------------
    # Contract search (conid resolution)
    # ------------------------------------------------------------------

    def get_conid(self, symbol: str) -> Optional[int]:
        """
        Resolve an LSE ticker symbol to its IBKR Contract ID (conid).

        Checks an in-process cache first.  If not cached, calls
        ``GET /iserver/secdef/search?symbol={symbol}&name=false&secType=STK``
        and filters for LSE or GBP contracts.

        Parameters
        ----------
        symbol:
            LSE ticker such as ``'HSBA'`` or ``'AZN'``.

        Returns
        -------
        int or None
            The conid integer, or None if not found.
        """
        # Cache hit
        cached = self._conid_cache.get(symbol)
        if cached:
            return cached

        data = self._get(
            "/iserver/secdef/search",
            params={"symbol": symbol, "name": "false", "secType": "STK"},
        )
        if not data or not isinstance(data, list):
            logger.warning("conid search returned no results for %s", symbol)
            return None

        # Prefer LSE / GBP contracts; fall back to first result.
        conid: Optional[int] = None
        for item in data:
            sections = item.get("sections", [])
            for section in sections:
                exch = section.get("exchange", "")
                if exch in ("LSE", "LSE.INTL"):
                    conid = int(item.get("conid", 0)) or None
                    if conid:
                        break
            if conid:
                break

        # Fallback: first item whose currency is GBP
        if conid is None:
            for item in data:
                currency = item.get("currency", "")
                if currency == "GBP":
                    conid = int(item.get("conid", 0)) or None
                    if conid:
                        break

        # Last resort: take the first result
        if conid is None and data:
            raw = data[0].get("conid")
            if raw:
                conid = int(raw)

        if conid:
            self._conid_cache[symbol] = conid
            logger.debug("Resolved conid for %s: %d", symbol, conid)
        else:
            logger.warning("Could not resolve conid for %s", symbol)

        return conid

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Fetch the latest last-traded price for a symbol.

        Calls ``GET /iserver/marketdata/snapshot?conids={conid}&fields=31``.
        The first call subscribes to the stream; a second call after 1 second
        returns populated data.

        Parameters
        ----------
        symbol:
            LSE ticker symbol.

        Returns
        -------
        float or None
            Last-traded price, or None if unavailable.
        """
        conid = self.get_conid(symbol)
        if not conid:
            return None

        params = {"conids": str(conid), "fields": "31,84,86"}

        # First call subscribes; second returns data.
        for attempt in range(2):
            data = self._get("/iserver/marketdata/snapshot", params=params)
            if data and isinstance(data, list) and len(data) > 0:
                item = data[0]
                raw_price = item.get("31")  # field 31 = last price
                if raw_price not in (None, "", "N/A"):
                    try:
                        price = float(str(raw_price).replace(",", ""))
                        if price > 0:
                            logger.debug("Price for %s: %.4f", symbol, price)
                            return price
                    except (ValueError, TypeError):
                        pass

                # Fallback: derive mid from bid/ask
                bid = item.get("84")
                ask = item.get("86")
                try:
                    if bid and ask and bid not in ("N/A", "") and ask not in ("N/A", ""):
                        mid = (float(str(bid).replace(",", "")) +
                               float(str(ask).replace(",", ""))) / 2.0
                        if mid > 0:
                            logger.debug(
                                "Mid price for %s (bid/ask): %.4f", symbol, mid
                            )
                            return mid
                except (ValueError, TypeError):
                    pass

            if attempt == 0:
                logger.debug(
                    "Snapshot not ready for %s on first call — retrying in 1s …",
                    symbol,
                )
                time.sleep(1)

        logger.warning("Could not obtain price for %s after 2 attempts.", symbol)
        return None

    def get_ohlcv_bars(
        self,
        symbol: str,
        period: str = "1d",
        bar: str = "5min",
    ) -> Optional[pd.DataFrame]:
        """
        Fetch historical OHLCV bars for a symbol.

        Calls
        ``GET /iserver/marketdata/history?conid={conid}&period={period}&bar={bar}``.

        Parameters
        ----------
        symbol:
            LSE ticker symbol.
        period:
            Lookback period, e.g. ``'1d'``, ``'5d'``, ``'1m'``.
        bar:
            Bar size, e.g. ``'5min'``, ``'1h'``, ``'1d'``.

        Returns
        -------
        pd.DataFrame or None
            Columns: ``open``, ``high``, ``low``, ``close``, ``volume``,
            ``timestamp``.  Returns None on failure.
        """
        conid = self.get_conid(symbol)
        if not conid:
            return None

        data = self._get(
            "/iserver/marketdata/history",
            params={
                "conid": str(conid),
                "period": period,
                "bar": bar,
                "outsideRth": "false",
            },
        )
        if not data or not isinstance(data, dict):
            logger.warning("No history data returned for %s", symbol)
            return None

        bars: List[Dict] = data.get("data", [])
        if not bars:
            logger.warning("Empty bars list for %s", symbol)
            return None

        rows = []
        for b in bars:
            try:
                rows.append(
                    {
                        "timestamp": pd.Timestamp(b["t"], unit="ms", tz="UTC"),
                        "open": float(b["o"]),
                        "high": float(b["h"]),
                        "low": float(b["l"]),
                        "close": float(b["c"]),
                        "volume": int(b.get("v", 0)),
                    }
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("Skipping malformed bar: %s — %s", b, exc)

        if not rows:
            logger.warning("No valid bars parsed for %s", symbol)
            return None

        df = pd.DataFrame(rows)
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        logger.debug(
            "OHLCV for %s: %d bars (period=%s, bar=%s)",
            symbol, len(df), period, bar,
        )
        return df

    # ------------------------------------------------------------------
    # Order placement helpers
    # ------------------------------------------------------------------

    def _submit_order(self, account_id: str, order_body: Dict) -> Optional[str]:
        """
        Submit an order payload and handle IBKR's confirmation reply step.

        IBKR CP API sometimes responds with a list containing an object that
        has an ``id`` field (a confirmation challenge).  We auto-confirm by
        POSTing to ``/iserver/reply/{id}`` with ``{"confirmed": true}``.

        Parameters
        ----------
        account_id:
            IBKR account ID.
        order_body:
            Full order request body (including ``orders`` list).

        Returns
        -------
        str or None
            Order ID string, or None on failure.
        """
        response = self._post(f"/iserver/account/{account_id}/orders", order_body)
        if response is None:
            return None

        # Normalize to list
        items: List[Dict] = response if isinstance(response, list) else [response]

        order_id: Optional[str] = None
        for item in items:
            if not isinstance(item, dict):
                continue

            # Confirmation challenge: item has an 'id' field (not 'order_id')
            if "id" in item and "order_id" not in item:
                reply_id = item["id"]
                logger.info(
                    "Order confirmation required (reply_id=%s) — auto-confirming.",
                    reply_id,
                )
                confirmed = self._post(
                    f"/iserver/reply/{reply_id}", {"confirmed": True}
                )
                if confirmed:
                    confirmed_list = (
                        confirmed if isinstance(confirmed, list) else [confirmed]
                    )
                    for c in confirmed_list:
                        if isinstance(c, dict) and c.get("order_id"):
                            order_id = str(c["order_id"])
                            break
                continue

            # Direct order_id in response
            if item.get("order_id"):
                order_id = str(item["order_id"])

        if order_id:
            logger.info("Order placed successfully: order_id=%s", order_id)
        else:
            logger.warning("Order placed but no order_id returned: %s", response)

        return order_id

    def place_market_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
    ) -> Optional[str]:
        """
        Place a market (MKT) order.

        Parameters
        ----------
        symbol:
            LSE ticker symbol.
        action:
            ``'BUY'`` or ``'SELL'``.
        quantity:
            Number of shares.

        Returns
        -------
        str or None
            Order ID string on success, or None on failure.
        """
        conid = self.get_conid(symbol)
        account_id = self._get_account_id()
        if not conid or not account_id:
            logger.error(
                "Cannot place MKT order for %s — missing conid or account_id.",
                symbol,
            )
            return None

        # Short-selling guard: cap SELL quantity at what IBKR actually holds
        from config import config
        if action.upper() == "SELL" and not config.risk.allow_short_selling:
            positions = self.get_positions()
            held_qty = int(positions.get(symbol, {}).get("quantity", 0))
            if held_qty <= 0:
                logger.warning(
                    "Short-selling blocked for %s — IBKR holds %d shares. "
                    "Set allow_short_selling=True to enable shorts.",
                    symbol, held_qty,
                )
                return None
            if quantity > held_qty:
                logger.warning(
                    "SELL quantity capped for %s: requested %d but only hold %d. "
                    "Selling %d to avoid short.",
                    symbol, quantity, held_qty, held_qty,
                )
                quantity = held_qty

        body = {
            "orders": [
                {
                    "conid": conid,
                    "orderType": "MKT",
                    "side": action.upper(),
                    "quantity": quantity,
                    "tif": "DAY",
                }
            ]
        }
        logger.info("Placing MKT %s %d x %s", action.upper(), quantity, symbol)
        return self._submit_order(account_id, body)

    def place_stop_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        stop_price: float,
    ) -> Optional[str]:
        """
        Place a stop (STP) order.

        Parameters
        ----------
        symbol:
            LSE ticker symbol.
        action:
            ``'BUY'`` or ``'SELL'``.
        quantity:
            Number of shares.
        stop_price:
            Stop trigger price.

        Returns
        -------
        str or None
            Order ID string on success, or None on failure.
        """
        conid = self.get_conid(symbol)
        account_id = self._get_account_id()
        if not conid or not account_id:
            logger.error(
                "Cannot place STP order for %s — missing conid or account_id.",
                symbol,
            )
            return None

        body = {
            "orders": [
                {
                    "conid": conid,
                    "orderType": "STP",
                    "side": action.upper(),
                    "quantity": quantity,
                    "auxPrice": round(stop_price, 4),
                    "tif": "DAY",
                }
            ]
        }
        logger.info(
            "Placing STP %s %d x %s stop=%.4f",
            action.upper(), quantity, symbol, stop_price,
        )
        return self._submit_order(account_id, body)

    def place_limit_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        limit_price: float,
    ) -> Optional[str]:
        """
        Place a limit (LMT) order.

        Parameters
        ----------
        symbol:
            LSE ticker symbol.
        action:
            ``'BUY'`` or ``'SELL'``.
        quantity:
            Number of shares.
        limit_price:
            Limit price for the order.

        Returns
        -------
        str or None
            Order ID string on success, or None on failure.
        """
        conid = self.get_conid(symbol)
        account_id = self._get_account_id()
        if not conid or not account_id:
            logger.error(
                "Cannot place LMT order for %s — missing conid or account_id.",
                symbol,
            )
            return None

        body = {
            "orders": [
                {
                    "conid": conid,
                    "orderType": "LMT",
                    "side": action.upper(),
                    "quantity": quantity,
                    "price": round(limit_price, 4),
                    "tif": "DAY",
                }
            ]
        }
        logger.info(
            "Placing LMT %s %d x %s limit=%.4f",
            action.upper(), quantity, symbol, limit_price,
        )
        return self._submit_order(account_id, body)

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order by its order ID.

        Calls ``DELETE /iserver/account/{accountId}/order/{orderId}``.

        Parameters
        ----------
        order_id:
            The order ID string returned by a place_* method.

        Returns
        -------
        bool
            True if the cancellation request was accepted, False otherwise.
        """
        account_id = self._get_account_id()
        if not account_id:
            logger.error("Cannot cancel order %s — account_id not available.", order_id)
            return False

        result = self._delete(f"/iserver/account/{account_id}/order/{order_id}")
        if result is not None:
            logger.info("Order %s cancelled: %s", order_id, result)
            return True
        return False

    def get_open_orders(self) -> List[Dict]:
        """
        Retrieve all open orders for the account.

        Calls ``GET /iserver/account/orders``.

        Returns
        -------
        list of dict
            Each dict contains order details.  Returns empty list on failure.
        """
        data = self._get("/iserver/account/orders")
        if not data:
            return []

        # Response is either a list directly or {"orders": [...]}
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("orders", [])
        return []

    # ------------------------------------------------------------------
    # Keepalive
    # ------------------------------------------------------------------

    def keepalive(self) -> None:
        """
        Send a keepalive ping to the CP Gateway.

        Calls ``POST /tickle``.  All errors are silently suppressed;
        only a DEBUG log is emitted.  Intended to be called on a timer
        (every ~60 seconds) to prevent session expiry.
        """
        try:
            with self._lock:
                resp = self._session.post(
                    f"{self.BASE_URL}/tickle", json={}, timeout=10
                )
            logger.debug("Keepalive tickle: status=%d", resp.status_code)
        except Exception as exc:
            logger.debug("Keepalive tickle failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Convenience / compatibility shims
    # ------------------------------------------------------------------

    def get_snapshot_price(self, symbol: str) -> Optional[float]:
        """Alias for get_current_price (drop-in replacement for TWS connector)."""
        return self.get_current_price(symbol)

    def is_connected(self) -> bool:
        """
        Compatibility shim for code that checks is_connected().

        Delegates to is_authenticated() — if the gateway is up and
        authenticated, the connection is considered live.
        """
        return self.is_authenticated()

    def connect(self) -> None:
        """
        Compatibility shim.  For the REST connector, 'connecting' means
        waiting for the CP Gateway to be authenticated.

        Raises
        ------
        ConnectionError
            If the gateway is not reachable / authenticated within 120 s.
        """
        if not self.wait_for_gateway(timeout_seconds=120):
            raise ConnectionError(
                "CP Gateway is not authenticated. "
                "Ensure IBeam is running and credentials are correct. "
                "If TOTP is not configured, open https://YOUR_VM_IP:5000 "
                "in a browser and log in once manually."
            )

    def disconnect(self) -> None:
        """
        Compatibility shim.  The REST connector has no persistent socket;
        this is a no-op provided for API compatibility with the TWS connector.
        """
        logger.info("IBKRConnector (REST): disconnect() called — no-op for HTTP connector.")

    def __repr__(self) -> str:
        status = "authenticated" if self.is_authenticated() else "unauthenticated"
        return f"<IBKRConnector (REST) gateway={self.BASE_URL} {status}>"
