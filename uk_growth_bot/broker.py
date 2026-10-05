"""Execution: a simulated paper broker, Trading 212 (default), and IBKR.

Live brokers only ever place long, cash, market orders — no margin, no
shorting, no leverage. ``execute`` returns (price_gbp, fees_gbp, order_id,
filled_quantity) or None.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
import urllib3

from . import universe as U
from .config import settings
from .planner import Order, fees

logger = logging.getLogger(__name__)

Fill = Tuple[float, float, str, float]
CashFlow = Tuple[date, float, str, str]   # day, signed amount, kind, unique reference


def normalise_gbp(price: float, reference_gbp: float) -> float:
    """Brokers often report London prices in pence; align with our GBP reference."""
    if reference_gbp > 0 and 50 < price / reference_gbp < 200:
        return round(price / 100, 4)
    return price


class PaperBroker:
    """Fills at the reference price plus modelled slippage."""

    def execute(self, order: Order) -> Optional[Fill]:
        slip = settings.slippage_pct if order.side == "BUY" else -settings.slippage_pct
        price = round(order.est_price * (1 + slip), 4)
        return price, fees(order.ticker, order.side, order.quantity * price), "paper", order.quantity

    def cash(self) -> Optional[float]:
        return None

    def positions(self) -> Optional[Dict[str, float]]:
        return None


class Trading212Broker:
    """Trading 212 public API v0 — API key + secret, no interactive login.

    Market orders are NOT idempotent (Trading 212's own warning), so an order
    request is never resent when its outcome is unknown (timeout, dropped
    connection). The bot looks for it in pending orders / history instead.
    Only requests the API definitely refused (validation error, 429) are retried.
    """

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.base = f"https://{settings.t212_env}.trading212.com/api/v0"
        self.s = session or requests.Session()
        if settings.t212_api_secret:
            self.s.auth = (settings.t212_api_key, settings.t212_api_secret)
        else:  # keys created before Trading 212 introduced secrets
            self.s.headers["Authorization"] = settings.t212_api_key
        self._wait_until: Dict[str, float] = {}
        self._map: Optional[Dict[str, str]] = None

    # --- transport ----------------------------------------------------
    def _call(self, method: str, path: str, **kw) -> Tuple[int, Any]:
        """(status, body). status 0 = no response (outcome unknown)."""
        key = method + re.sub(r"/\d+$", "/{id}", path.split("?")[0])
        for attempt in range(2):
            wait = self._wait_until.get(key, 0) - time.time()
            if wait > 0:
                time.sleep(min(wait, 65))
            try:
                r = self.s.request(method, self.base + path, timeout=30, **kw)
            except requests.RequestException as exc:
                logger.error("Trading 212 %s %s: no response (%s)", method, path, exc)
                return 0, None
            remaining, reset = r.headers.get("x-ratelimit-remaining"), r.headers.get("x-ratelimit-reset")
            if remaining is not None and reset and int(float(remaining)) <= 0:
                self._wait_until[key] = float(reset)
            if r.status_code == 429 and attempt == 0:
                self._wait_until[key] = float(reset) if reset else time.time() + 10
                continue  # a 429 was refused, never executed — safe to retry
            try:
                body = r.json()
            except ValueError:
                body = r.text
            if r.status_code >= 400 and r.status_code != 404:
                logger.warning("Trading 212 %s %s -> %d %s", method, path, r.status_code, str(body)[:300])
            return r.status_code, body
        return 429, None

    def _get(self, path: str) -> Optional[Any]:
        status, body = self._call("GET", path)
        return body if status == 200 else None

    def _paged(self, path: str, pages: int = 3) -> List[Dict]:
        items: List[Dict] = []
        nxt: Optional[str] = path
        for _ in range(pages):
            if not nxt:
                break
            body = self._get(nxt) or {}
            items += body.get("items", [])
            nxt = body.get("nextPagePath")
            if nxt and nxt.startswith("/api/v0"):
                nxt = nxt[len("/api/v0"):]
        return items

    # --- account -------------------------------------------------------
    def ready(self) -> bool:
        status, body = self._call("GET", "/equity/account/summary")
        if status in (401, 403):
            logger.error("Trading 212 rejected the API key (%d). Check T212_API_KEY/SECRET, that the key "
                         "was made for this account (%s environment) and its permissions.",
                         status, settings.t212_env)
        return status == 200

    def cash(self) -> Optional[float]:
        body = self._get("/equity/account/summary")
        return float(body["cash"]["availableToTrade"]) if body else None

    def cash_flows(self) -> List[CashFlow]:
        """Deposits, withdrawals, fees and dividends, straight from Trading 212."""
        out: List[CashFlow] = []
        kinds = {"DEPOSIT": "contribution", "WITHDRAW": "withdrawal", "FEE": "fee", "TRANSFER": "contribution"}
        for t in self._paged("/equity/history/transactions?limit=50"):
            kind = kinds.get(t.get("type", ""))
            if not kind:
                continue
            amount = abs(float(t.get("amount", 0)))
            if kind in ("withdrawal", "fee"):
                amount = -amount
            out.append((_day(t.get("dateTime")), amount, kind, f"t212:{t.get('reference')}"))
        for d in self._paged("/equity/history/dividends?limit=50"):
            out.append((_day(d.get("paidOn")), float(d.get("amount", 0)), "dividend",
                        f"t212:{d.get('reference')}"))
        return out

    # --- instruments ---------------------------------------------------
    def instrument_map(self) -> Dict[str, str]:
        """Our ticker (e.g. VUAG.L) -> Trading 212 ticker (e.g. VUAGl_EQ)."""
        if self._map is not None:
            return self._map
        cache = os.path.join(settings.data_dir, "t212_instruments.json")
        insts = None
        if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 86400:
            with open(cache) as fh:
                insts = json.load(fh)
        if insts is None:
            insts = self._get("/equity/metadata/instruments") or []
            if insts:
                os.makedirs(settings.data_dir, exist_ok=True)
                with open(cache, "w") as fh:
                    json.dump(insts, fh)
        overrides = dict(kv.split("=", 1) for kv in
                         filter(None, os.getenv("UK_T212_TICKERS", "").replace(" ", "").split(",")))
        self._map = {}
        for ours in U.ALL:
            if ours in overrides:
                self._map[ours] = overrides[ours]
                continue
            sym = ours.split(".")[0]
            exact = [i for i in insts if i.get("ticker") == f"{sym}l_EQ"]
            loose = [i for i in insts if str(i.get("shortName", "")).upper() == sym
                     and i.get("currencyCode") in ("GBP", "GBX")]
            match = (exact or loose)[:1]
            if match:
                self._map[ours] = match[0]["ticker"]
            else:
                logger.warning("No Trading 212 instrument for %s; set UK_T212_TICKERS=%s=<ticker>", ours, ours)
        return self._map

    def positions(self) -> Optional[Dict[str, float]]:
        rows = self._get("/equity/positions")
        if rows is None:
            return None
        back = {v: k for k, v in self.instrument_map().items()}
        out: Dict[str, float] = {}
        for p in rows:
            t212 = p.get("instrument", {}).get("ticker") or p.get("ticker")
            out[back.get(t212, t212)] = float(p.get("quantity", 0))
        return {k: v for k, v in out.items() if v}

    # --- orders --------------------------------------------------------
    def execute(self, order: Order) -> Optional[Fill]:
        ticker = self.instrument_map().get(order.ticker)
        if not ticker:
            return None
        if order.side == "SELL":
            held = (self.positions() or {}).get(order.ticker, 0.0)
            if held + 1e-9 < order.quantity:
                logger.error("Refusing to sell %s %s: Trading 212 holds %s", order.quantity, order.ticker, held)
                return None
        for decimals in sorted({settings.qty_decimals, 1, 0}, reverse=True):
            f = 10 ** decimals
            qty = math.floor(order.quantity * f + 1e-9) / f
            if qty <= 0:
                break
            signed = qty if order.side == "BUY" else -qty
            sent_at = time.time()
            status, body = self._call("POST", "/equity/orders/market",
                                      json={"ticker": ticker, "quantity": signed, "extendedHours": False})
            if status == 200 and isinstance(body, dict) and body.get("id"):
                return self._await_fill(int(body["id"]), ticker, order)
            if status in (0, 408, 500, 502, 503, 504):
                found = self._find_recent(ticker, signed, sent_at)
                if found:
                    return self._await_fill(found, ticker, order)
                logger.error("Order for %s %s has an UNKNOWN outcome — not retrying (orders are not "
                             "idempotent). Check the Trading 212 app.", signed, ticker)
                return None
            if status == 400 and re.search(r"precision|decimal", str(body), re.I):
                continue  # refused for too many decimals: retry coarser
            return None
        return None

    def _find_recent(self, ticker: str, signed: float, since: float) -> Optional[int]:
        time.sleep(5)
        candidates = list(self._get("/equity/orders") or [])
        candidates += [i.get("order", {}) for i in self._paged(f"/equity/history/orders?ticker={ticker}&limit=20", 1)]
        side = "BUY" if signed > 0 else "SELL"
        for o in candidates:
            if (o.get("ticker") == ticker and abs(abs(float(o.get("quantity") or 0)) - abs(signed)) < 1e-9
                    and o.get("side", side) == side and o.get("initiatedFrom") in (None, "API")
                    and _ts(o.get("createdAt")) >= since - 120):
                return int(o["id"])
        return None

    def _await_fill(self, oid: int, ticker: str, order: Order) -> Optional[Fill]:
        for _ in range(150):  # up to ~5 minutes while the order is pending
            status, body = self._call("GET", f"/equity/orders/{oid}")
            if status == 404:
                break  # left the pending list: filled, cancelled or rejected
            if status == 200 and body.get("status") in ("CANCELLED", "REJECTED"):
                logger.error("Order %s for %s was %s", oid, ticker, body.get("status"))
                return None
            time.sleep(2)
        else:
            logger.error("Order %s for %s still pending after 5 min — check Trading 212", oid, ticker)
            return None
        for _ in range(5):  # history can lag a few seconds behind
            for item in self._paged(f"/equity/history/orders?ticker={ticker}&limit=20", 1):
                o, fill = item.get("order", {}), item.get("fill") or {}
                if int(o.get("id", -1)) != oid:
                    continue
                if o.get("status") not in ("FILLED", "PARTIALLY_FILLED"):
                    logger.error("Order %s for %s ended %s", oid, ticker, o.get("status"))
                    return None
                qty = abs(float(fill.get("quantity") or o.get("filledQuantity") or 0))
                raw = float(fill.get("price") or (abs(float(o.get("filledValue") or 0)) / qty if qty else 0))
                price = normalise_gbp(raw, order.est_price)
                taxes = (fill.get("walletImpact") or {}).get("taxes") or []
                charged = round(sum(abs(float(t.get("quantity", 0))) for t in taxes), 2)
                return price, charged, str(oid), qty
            time.sleep(12)
        logger.error("Order %s for %s filled but not found in history — check Trading 212", oid, ticker)
        return None


def _ts(iso: Optional[str]) -> float:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _day(iso: Optional[str]) -> date:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).date()
    except ValueError:
        return date.today()


class IBKRBroker:
    """IBKR Client Portal REST client (gateway kept logged in by IBeam).

    Not the default: IBKR sessions reset daily and every login needs a 2FA
    approval on your phone, so this is only semi-autonomous.
    """

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

    def execute(self, order: Order) -> Optional[Fill]:
        acct, cid = self.account(), self.conid(order.ticker)
        if not acct or not cid:
            return None
        if order.side == "SELL":
            held = (self.positions() or {}).get(order.ticker, 0.0)
            if held < order.quantity:
                logger.error("Refusing to sell %s %s: IBKR holds %s", order.quantity, order.ticker, held)
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

    def _await_fill(self, oid: str, order: Order) -> Optional[Fill]:
        for _ in range(60):
            st = self._req("GET", f"/iserver/account/order/status/{oid}") or {}
            status = str(st.get("order_status") or st.get("status") or "").lower()
            if status == "filled":
                raw = float(st.get("average_price") or st.get("avgPrice") or order.est_price)
                price = normalise_gbp(raw, order.est_price)
                return price, fees(order.ticker, order.side, order.quantity * price), oid, float(int(order.quantity))
            if status in ("cancelled", "inactive", "rejected"):
                logger.error("Order %s for %s ended %s", oid, order.ticker, status)
                return None
            time.sleep(5)
        logger.error("Order %s for %s not filled after 5 min — check IBKR", oid, order.ticker)
        return None


def make_broker():
    if settings.mode != "live":
        return PaperBroker()
    return Trading212Broker() if settings.broker == "trading212" else IBKRBroker()
