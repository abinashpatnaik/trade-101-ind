"""SQLite ledger: every buy/sell, cash flow and NAV snapshot.

The ledger is the bot's own record for tax and performance, and the only
record of which Zerodha cash and shares belong to this bot (the account is
shared with the intraday agent). Zerodha's contract notes are the authority.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Dict, List, Optional, Tuple

from .config import settings
from .tax import Txn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS txns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
    quantity REAL NOT NULL, price REAL NOT NULL, fees REAL NOT NULL DEFAULT 0,
    reason TEXT, order_id TEXT, mode TEXT NOT NULL, fx REAL NOT NULL DEFAULT 0
);
-- kind: contribution | withdrawal
CREATE TABLE IF NOT EXISTS cash_flows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL, amount REAL NOT NULL, kind TEXT NOT NULL, note TEXT, mode TEXT NOT NULL,
    UNIQUE(day, kind, note, mode)
);
CREATE TABLE IF NOT EXISTS nav_history (
    day TEXT NOT NULL, mode TEXT NOT NULL, nav REAL NOT NULL, cash REAL NOT NULL,
    net_contributions REAL NOT NULL, PRIMARY KEY (day, mode)
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, mode TEXT NOT NULL, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Ledger:
    def __init__(self, path: Optional[str] = None, mode: Optional[str] = None) -> None:
        self.mode = mode or settings.mode
        self.path = path or os.path.join(settings.data_dir, "in_growth.db")
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- transactions -------------------------------------------------
    def record_txn(self, t: Txn, reason: str = "", order_id: str = "") -> None:
        with self._conn() as c:
            c.execute("INSERT INTO txns (day, symbol, side, quantity, price, fees, reason, order_id, mode, fx)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (t.day.isoformat(), t.symbol, t.side, t.quantity, t.price, t.fees,
                       reason, order_id, self.mode, t.fx))

    def txns(self) -> List[Txn]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM txns WHERE mode=? ORDER BY day, id", (self.mode,)).fetchall()
        return [Txn(date.fromisoformat(r["day"]), r["symbol"], r["side"], r["quantity"],
                    r["price"], r["fees"], r["fx"]) for r in rows]

    def txn_rows(self, since: date) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM txns WHERE mode=? AND day>=? ORDER BY day, id",
                             (self.mode, since.isoformat())).fetchall()
        return [dict(r) for r in rows]

    def holdings(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for t in self.txns():
            out[t.symbol] = out.get(t.symbol, 0.0) + (t.quantity if t.side == "BUY" else -t.quantity)
        return {s: q for s, q in out.items() if q > 1e-9}

    def first_buy_day(self, symbol: str) -> Optional[date]:
        """Start of the current continuous holding period of *symbol*."""
        qty, start = 0.0, None
        for t in self.txns():
            if t.symbol != symbol:
                continue
            if t.side == "BUY" and qty <= 1e-9:
                start = t.day
            qty += t.quantity if t.side == "BUY" else -t.quantity
        return start if qty > 1e-9 else None

    # --- cash ---------------------------------------------------------
    def record_cash_flow(self, day: date, amount: float, kind: str, note: str = "") -> bool:
        with self._conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO cash_flows (day, amount, kind, note, mode) VALUES (?,?,?,?,?)",
                            (day.isoformat(), round(amount, 2), kind, note, self.mode))
            return cur.rowcount > 0

    def cash_flows(self, kind: Optional[str] = None) -> List[Tuple[date, float, str]]:
        q, args = "SELECT day, amount, kind FROM cash_flows WHERE mode=?", [self.mode]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        with self._conn() as c:
            rows = c.execute(q + " ORDER BY day", args).fetchall()
        return [(date.fromisoformat(r["day"]), r["amount"], r["kind"]) for r in rows]

    def net_contributions(self) -> float:
        return sum(a for _, a, k in self.cash_flows() if k in ("contribution", "withdrawal"))

    def cash(self) -> float:
        """Ledger cash: flows in, minus buys, plus sells, net of fees."""
        bal = sum(a for _, a, _ in self.cash_flows())
        for t in self.txns():
            value = t.quantity * t.price
            bal += (value - t.fees) if t.side == "SELL" else -(value + t.fees)
        return round(bal, 2)

    # --- NAV / decisions / state ---------------------------------------
    def record_nav(self, day: date, nav: float, cash: float) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO nav_history (day, mode, nav, cash, net_contributions)"
                      " VALUES (?,?,?,?,?)", (day.isoformat(), self.mode, round(nav, 2), round(cash, 2),
                                              round(self.net_contributions(), 2)))

    def nav_history(self) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM nav_history WHERE mode=? ORDER BY day", (self.mode,)).fetchall()
        return [dict(r) for r in rows]

    def log_decision(self, day: date, text: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO decisions (day, mode, text) VALUES (?,?,?)", (day.isoformat(), self.mode, text))

    def decisions(self, since: date) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute("SELECT day, text FROM decisions WHERE mode=? AND day>=? ORDER BY id",
                             (self.mode, since.isoformat())).fetchall()
        return [dict(r) for r in rows]

    def get_state(self, key: str, default=None):
        with self._conn() as c:
            row = c.execute("SELECT value FROM state WHERE key=?", (f"{self.mode}:{key}",)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, key: str, value) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?,?)",
                      (f"{self.mode}:{key}", json.dumps(value)))
