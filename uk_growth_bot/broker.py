"""Execution: a simulated paper broker, and IBKR via the Client Portal Gateway.

The live broker only ever places long, cash, market orders in whole shares —
no margin, no shorting, no leverage.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Dict, Optional, Tuple

import requests
import urllib3

from .config import settings
from .planner import Order, fees

logger = logging.getLogger(__name__)


class PaperBroker:
    """Fills at the reference price plus modelled slippage."""

    def execute(self, order: Order) -> Optional[Tuple[float, float, str]]:
        slip = settings.slippage_pct if order.side == "BUY" else -settings.slippage_pct
        price = round(order.est_price * (1 + slip), 4)
        return price, fees(order.ticker, order.side, order.quantity * price), "paper"

    def cash(self) -> Optional[float]:
        return None

    def positions(self) -> Optional[Dict[str, float]]:
        return None


class IBKRBroker:
    """Minimal IBKR Client Portal REST client (gateway kept logged in by IBeam)."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base = (base_url or settings.ibkr_gateway_url).rstrip("/") + "/v1/api"
        self.s = requests.Session()
        # The gateway serves a self-signed cert on the internal docker network.
        self.s.verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._account: Optional[str] = None
        self._conids: Dict[str, int] = {}

    def _req(self, method: str, path: str, **kw):
        try:
            r = self.s.request(method, self.base + path, timeout=20, **kw)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.error("IBKR %s %s failed: %s", method, path, exc)
            return None

    def ready(self, timeout: int = 120) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st = self._req("GET", "/iserver/auth/status") or {}
            if st.get("authenticated"):
                return True
            time.sleep(5)
        return False

    def account(self) -> Optional[str]:
        if not self._account:
            accts = self._req("GET", "/portfolio/accounts") or []
            self._account = str(accts[0]["id"]) if accts else None
        return self._account

    def conid(self, ticker: str) -> Optional[int]:
        sym = ticker.split(".")[0]
        if sym in self._conids:
            return self._conids[sym]
        res = self._req("GET", "/iserver/secdef/search", params={"symbol": sym, "secType": "STK"}) or []
        for item in res:
            if any(sec.get("exchange") in ("LSE", "LSEETF") for sec in item.get("sections", [])) or \
                    "LSE" in str(item.get("description", "")):
                self._conids[sym] = int(item["conid"])
                return self._conids[sym]
        logger.error("No LSE contract found for %s", ticker)
        return None

    def cash(self) -> Optional[float]:
        acct = self.account()
        summ = self._req("GET", f"/portfolio/{acct}/summary") if acct else None
        if not summ:
            return None
        return float((summ.get("totalcashvalue") or summ.get("availablefunds") or {}).get("amount", 0.0))

    def positions(self) -> Optional[Dict[str, float]]:
        acct = self.account()
        rows = self._req("GET", f"/portfolio/{acct}/positions/0") if acct else None
        if rows is None:
            return None
        return {f"{str(p.get('ticker') or p.get('contractDesc')).split()[0]}.L": float(p.get("position", 0))
                for p in rows if float(p.get("position", 0)) != 0}

    def execute(self, order: Order) -> Optional[Tuple[float, float, str]]:
        acct, cid = self.account(), self.conid(order.ticker)
        if not acct or not cid:
            return None
        if order.side == "SELL":
            held = (self.positions() or {}).get(order.ticker, 0.0)
            if held < order.quantity:
                logger.error("Refusing to sell %d %s: IBKR holds %s", order.quantity, order.ticker, held)
                return None
        body = {"orders": [{"conid": cid, "orderType": "MKT", "side": order.side,
                            "quantity": int(order.quantity), "tif": "DAY", "outsideRTH": False}]}
        resp = self._req("POST", f"/iserver/account/{acct}/orders", json=body)
        # IBKR answers with confirmation prompts ("are you sure?") — accept them.
        for _ in range(5):
            items = resp if isinstance(resp, list) else [resp] if resp else []
            oid = next((str(i["order_id"]) for i in items if isinstance(i, dict) and i.get("order_id")), None)
            if oid:
                return self._await_fill(oid, order)
            reply = next((i["id"] for i in items if isinstance(i, dict) and "id" in i), None)
            if not reply:
                break
            resp = self._req("POST", f"/iserver/reply/{reply}", json={"confirmed": True})
        logger.error("Order not accepted for %s: %s", order.ticker, resp)
        return None

    def _await_fill(self, oid: str, order: Order) -> Optional[Tuple[float, float, str]]:
        for _ in range(60):
            st = self._req("GET", f"/iserver/account/order/status/{oid}") or {}
            status = str(st.get("order_status") or st.get("status") or "").lower()
            if status == "filled":
                raw = float(st.get("average_price") or st.get("avgPrice") or order.est_price)
                price = normalise_gbp(raw, order.est_price)
                return price, fees(order.ticker, order.side, order.quantity * price), oid
            if status in ("cancelled", "inactive", "rejected"):
                logger.error("Order %s for %s ended %s", oid, order.ticker, status)
                return None
            time.sleep(5)
        logger.error("Order %s for %s not filled after 5 min — check IBKR", oid, order.ticker)
        return None


def normalise_gbp(price: float, reference_gbp: float) -> float:
    """IBKR can report London prices in pence; align with our GBP reference."""
    if reference_gbp > 0 and 50 < price / reference_gbp < 200:
        return round(price / 100, 4)
    return price


def make_broker():
    return IBKRBroker() if settings.mode == "live" else PaperBroker()


def is_contribution_day(today: date) -> bool:
    return today.day >= settings.contribution_day
