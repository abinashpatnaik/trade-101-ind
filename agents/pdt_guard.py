"""
agents.pdt_guard
================
Pattern Day Trader protection for sub-$25K US margin accounts.

FINRA's PDT rule: 4+ day trades (same-symbol buy and sell on the same
session) within 5 rolling business days flags the account and freezes it
for 90 days. Alpaca enforces this broker-side by rejecting the offending
order — which would strand a position with no working protective exit.

Design: enforce on the ENTRY side. A new BUY needs an available day-trade
slot, because its protective exits (stop-loss, trailing stop) may need to
close it the same day. Slots:

    available = max_day_trades - day_trades_used(last 5 business days)
                - open positions bought today (each reserves a slot)

Exits are never blocked — every open position holds a reservation, so a
same-day close always has a slot to consume.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Set

logger = logging.getLogger(__name__)


def last_n_business_days(n: int, today: date = None) -> Set[str]:
    """The last *n* weekdays including today, as YYYY-MM-DD strings.
    (Exchange holidays count conservatively as business days — being
    slightly stricter than FINRA is the safe direction.)"""
    today = today or date.today()
    days: List[str] = []
    d = today
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return set(days)


def count_day_trades(trades: Iterable[Dict], window_days: Set[str]) -> int:
    """
    Count day trades in the window: for each (date, symbol) with both a BUY
    and a SELL, count min(#buys, #sells) round trips — FINRA counts each
    paired open+close as one day trade.
    """
    buys: Dict[tuple, int] = {}
    sells: Dict[tuple, int] = {}
    for t in trades:
        t_date = str(t.get("date", ""))
        if t_date not in window_days:
            continue
        key = (t_date, t.get("symbol"))
        action = str(t.get("action", "")).upper()
        if action == "BUY":
            buys[key] = buys.get(key, 0) + 1
        elif action == "SELL":
            sells[key] = sells.get(key, 0) + 1
    return sum(min(n, sells.get(key, 0)) for key, n in buys.items())


class PDTGuard:
    """Entry-side day-trade budget for one trading session."""

    def __init__(self, db, max_day_trades: int = 3) -> None:
        self._db = db
        self._max = max_day_trades
        self._opened_today: Set[str] = set()

    def note_buy(self, symbol: str) -> None:
        """Record that *symbol* was opened this session (reserves a slot)."""
        self._opened_today.add(symbol)

    def slots_available(self) -> int:
        try:
            trades = self._db.get_trades(limit=2000)
        except Exception as exc:
            logger.warning("PDTGuard: DB read failed (%s) — assuming 0 slots.", exc)
            return 0
        window = last_n_business_days(5)
        used = count_day_trades(trades, window)
        today = date.today().strftime("%Y-%m-%d")
        # Positions opened today that HAVEN'T closed yet still reserve a slot;
        # closed ones are already inside `used`.
        closed_today = {
            t.get("symbol")
            for t in trades
            if str(t.get("date")) == today and str(t.get("action", "")).upper() == "SELL"
        }
        reserved = len(self._opened_today - closed_today)
        return max(0, self._max - used - reserved)

    def can_open_new_position(self) -> bool:
        available = self.slots_available()
        if available <= 0:
            logger.info(
                "PDT guard: no day-trade slots available (max %d per 5 business "
                "days) — new entries blocked to protect same-day exits.",
                self._max,
            )
            return False
        return True
