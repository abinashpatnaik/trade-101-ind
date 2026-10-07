"""Execution: a simulated broker (sim mode) and Zerodha Kite Connect (live).

The live broker only places long, cash (CNC delivery) LIMIT orders on NSE,
priced a little through the last price so they fill like market orders
without paying any price. No margin, no shorting, no intraday products.
``execute`` returns (avg_price, fees, order_id, filled_quantity) or None.

The Zerodha account is shared with the intraday agent. This bot reuses that
agent's cached Kite access token (logging in itself only when it is missing
or expired, with the same KITE_* credentials) so the two never knock each
other's session out.
"""

from __future__ import annotations

import logging
import math
import os
import time
import urllib.parse
from datetime import datetime
from typing import Dict, Optional, Tuple

from . import universe as U
from .config import settings
from .planner import Order, fees

logger = logging.getLogger(__name__)

Fill = Tuple[float, float, str, float]
ORDER_TAG = "ingrowth"
FILL_WAIT_SECONDS = 90


def tick_size(price: float) -> float:
    """Coarsest NSE tick for the price band. Coarser ticks are multiples of
    finer ones, so a price on this grid is valid whichever schedule applies."""
    for limit, tick in ((250, 0.01), (1000, 0.05), (5000, 0.10), (10000, 0.50), (20000, 1.00)):
        if price < limit:
            return tick
    return 5.00


def limit_price(ltp: float, side: str) -> float:
    """Marketable limit: a little through the last price, on the tick grid."""
    if side == "BUY":
        raw = ltp * (1 + settings.limit_buffer_pct)
        tick = tick_size(raw)
        return round(math.ceil(raw / tick - 1e-9) * tick, 2)
    raw = ltp * (1 - settings.limit_buffer_pct)
    tick = tick_size(raw)
    return round(math.floor(raw / tick + 1e-9) * tick, 2)


class SimBroker:
    """Fills at the reference price plus modelled slippage."""

    def execute(self, order: Order) -> Optional[Fill]:
        slip = settings.slippage_pct if order.side == "BUY" else -settings.slippage_pct
        price = round(order.est_price * (1 + slip), 2)
        return price, fees(order.ticker, order.side, order.quantity * price), "sim", order.quantity

    def ready(self) -> bool:
        return True

    def cash(self) -> Optional[float]:
        return None

    def positions(self) -> Optional[Dict[str, float]]:
        return None


class KiteBroker:
    def __init__(self, kite=None, session_factory=None) -> None:
        self.api_key = os.getenv("KITE_API_KEY", "").strip()
        self.api_secret = os.getenv("KITE_API_SECRET", "").strip()
        self.user_id = os.getenv("KITE_USER_ID", "").strip()
        self.password = os.getenv("KITE_PASSWORD", "").strip()
        self.totp_secret = os.getenv("KITE_TOTP_SECRET", "").strip()
        self.token_file = os.path.join(settings.shared_dir, "kite_access_token.txt")
        self.kite = kite
        self._session_factory = session_factory
        self._ok = False

    # --- session -------------------------------------------------------
    def _client(self):
        if self.kite is None:
            from kiteconnect import KiteConnect
            self.kite = KiteConnect(api_key=self.api_key)
        return self.kite

    def _valid(self) -> bool:
        try:
            self._client().profile()
            return True
        except Exception as exc:
            logger.info("Kite session not valid: %s", exc)
            return False

    def _login(self) -> Optional[str]:
        """Automated login (password + TOTP), the same flow the intraday agent uses."""
        if not (self.user_id and self.password and self.totp_secret and self.api_secret):
            logger.error("Kite login needs KITE_USER_ID, KITE_PASSWORD, KITE_TOTP_SECRET and KITE_API_SECRET.")
            return None
        import pyotp
        import requests
        kite = self._client()
        session = self._session_factory() if self._session_factory else requests.Session()
        login_url = kite.login_url()
        session.get(login_url, timeout=20)
        r = session.post("https://kite.zerodha.com/api/login",
                         data={"user_id": self.user_id, "password": self.password}, timeout=20).json()
        if r.get("status") != "success":
            logger.error("Kite login refused: %s", r.get("message"))
            return None
        r = session.post("https://kite.zerodha.com/api/twofa", timeout=20,
                         data={"user_id": self.user_id, "request_id": r["data"]["request_id"],
                               "twofa_value": pyotp.TOTP(self.totp_secret).now(), "twofa_type": "totp"}).json()
        if r.get("status") != "success":
            logger.error("Kite 2FA refused: %s", r.get("message"))
            return None
        try:
            url = session.get(login_url + "&skip_session=true", allow_redirects=True, timeout=20).url
        except requests.exceptions.ConnectionError as e:   # redirect to the app's (unreachable) URL
            if not e.request:
                raise
            url = e.request.url
        token = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("request_token", [None])[0]
        if not token:
            logger.error("Kite login: no request_token in the redirect.")
            return None
        return kite.generate_session(token, api_secret=self.api_secret)["access_token"]

    def ready(self) -> bool:
        if self._ok:
            return True
        if not self.api_key:
            logger.error("KITE_API_KEY is not set.")
            return False
        kite = self._client()
        if os.path.exists(self.token_file):
            with open(self.token_file) as fh:
                cached = fh.read().strip()
            if cached:
                kite.set_access_token(cached)
                if self._valid():
                    self._ok = True
                    return True
        try:
            token = self._login()
        except Exception as exc:
            logger.error("Kite login failed: %s", exc)
            token = None
        if not token:
            return False
        kite.set_access_token(token)
        os.makedirs(settings.shared_dir, exist_ok=True)
        with open(self.token_file, "w") as fh:   # shared: the intraday agent reuses it
            fh.write(token)
        self._ok = self._valid()
        return self._ok

    # --- account -------------------------------------------------------
    def cash(self) -> Optional[float]:
        try:
            eq = self._client().margins("equity")
            return float(eq.get("net", 0.0))
        except Exception as exc:
            logger.error("Kite margins failed: %s", exc)
            return None

    def positions(self) -> Optional[Dict[str, float]]:
        """Every NSE delivery holding in the account (incl. today's buys), by Yahoo ticker."""
        try:
            kite = self._client()
            out: Dict[str, float] = {}
            for h in kite.holdings():
                if h.get("exchange", "NSE") != "NSE":
                    continue
                q = float(h.get("quantity", 0)) + float(h.get("t1_quantity", 0))
                out[f"{h['tradingsymbol']}.NS"] = out.get(f"{h['tradingsymbol']}.NS", 0.0) + q
            for p in kite.positions().get("net", []):
                if p.get("product") == "CNC" and p.get("exchange", "NSE") == "NSE":
                    t = f"{p['tradingsymbol']}.NS"
                    out[t] = out.get(t, 0.0) + float(p.get("quantity", 0))
            return {t: q for t, q in out.items() if q > 0}
        except Exception as exc:
            logger.error("Kite holdings failed: %s", exc)
            return None

    def tradable(self, tickers) -> Optional[set]:
        """Which tickers Kite can quote (None if the lookup failed)."""
        try:
            keys = [f"NSE:{U.ALL[t].symbol}" for t in tickers if t in U.ALL]
            got = self._client().ltp(keys) if keys else {}
            return {t for t in tickers if t in U.ALL and f"NSE:{U.ALL[t].symbol}" in got}
        except Exception as exc:
            logger.warning("Kite instrument lookup failed: %s", exc)
            return None

    def _ltp(self, symbol: str) -> Optional[float]:
        try:
            q = self._client().ltp(f"NSE:{symbol}")
            return float(q[f"NSE:{symbol}"]["last_price"])
        except Exception as exc:
            logger.warning("Kite LTP for %s failed (%s); using the research price", symbol, exc)
            return None

    # --- orders --------------------------------------------------------
    def _find_recent(self, symbol: str, side: str, qty: int, since: float) -> Optional[str]:
        try:
            for o in reversed(self._client().orders()):
                ts = o.get("order_timestamp")
                when = ts.timestamp() if isinstance(ts, datetime) else since
                if (o.get("tradingsymbol") == symbol and o.get("transaction_type") == side
                        and int(o.get("quantity", 0)) == qty and o.get("tag") == ORDER_TAG
                        and when >= since - 5):
                    return str(o["order_id"])
        except Exception as exc:
            logger.error("Kite order lookup failed: %s", exc)
        return None

    def _await_fill(self, order_id: str) -> Tuple[float, float]:
        """(filled_qty, avg_price). Cancels whatever is still open after the wait."""
        kite = self._client()
        deadline = time.monotonic() + FILL_WAIT_SECONDS
        last: Dict = {}
        while True:
            try:
                hist = kite.order_history(order_id)
                last = hist[-1] if hist else last
            except Exception as exc:
                logger.warning("Kite order_history %s: %s", order_id, exc)
            status = str(last.get("status", "")).upper()
            if status in ("COMPLETE", "REJECTED", "CANCELLED"):
                break
            if time.monotonic() >= deadline:
                try:
                    kite.cancel_order(variety="regular", order_id=order_id)
                    logger.warning("Kite order %s not filled in %ss; cancelled the rest.", order_id, FILL_WAIT_SECONDS)
                    time.sleep(2)
                    hist = kite.order_history(order_id)
                    last = hist[-1] if hist else last
                except Exception as exc:
                    logger.error("Cancelling Kite order %s failed: %s", order_id, exc)
                break
            time.sleep(2)
        if str(last.get("status", "")).upper() == "REJECTED":
            logger.error("Kite rejected order %s: %s", order_id, last.get("status_message"))
        return float(last.get("filled_quantity") or 0), float(last.get("average_price") or 0)

    def execute(self, order: Order) -> Optional[Fill]:
        if order.ticker not in U.ALL:
            return None
        symbol = U.ALL[order.ticker].symbol
        qty = int(order.quantity)
        if qty <= 0:
            return None
        if order.side == "SELL":
            held = (self.positions() or {}).get(order.ticker, 0.0)
            if held + 1e-9 < qty:
                logger.error("Refusing to sell %d %s: Zerodha holds %g", qty, order.ticker, held)
                return None
        ltp = self._ltp(symbol) or order.est_price
        price = limit_price(ltp, order.side)
        sent_at = time.time()
        try:
            oid = str(self._client().place_order(
                variety="regular", exchange="NSE", tradingsymbol=symbol, transaction_type=order.side,
                quantity=qty, product="CNC", order_type="LIMIT", price=price, validity="DAY", tag=ORDER_TAG))
        except Exception as exc:
            # The request may or may not have reached Zerodha. Never resend;
            # look for it in today's order book instead.
            logger.error("Kite place_order %s %d %s failed: %s", order.side, qty, symbol, exc)
            time.sleep(3)
            oid = self._find_recent(symbol, order.side, qty, sent_at)
            if not oid:
                return None
            logger.warning("Order was placed after all: %s", oid)
        filled, avg = self._await_fill(oid)
        if filled <= 0 or avg <= 0:
            return None
        return avg, fees(order.ticker, order.side, filled * avg), oid, filled


def make_broker():
    return KiteBroker() if settings.uses_broker else SimBroker()
