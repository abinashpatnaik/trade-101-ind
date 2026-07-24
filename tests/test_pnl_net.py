"""
Net-of-cost P&L accounting.

The dashboard's realized chart, win rate, profit factor and lifetime figure
were all derived from the GROSS ``trades.pnl`` column (entry vs exit price,
no charges), so they overstated performance by the full cost of trading. On
2026-07-24 the bot's own reconcile logged the gap: recorded gross ₹108.96 vs
broker day P&L ₹64.45. ``pnl_net`` deducts the modelled round-trip friction so
these numbers tie out far closer to the real account.

The broker's day P&L stays authoritative; pnl_net is the honest approximation
for historical per-trade figures (it cannot capture per-fill slippage).
"""

import os
import sqlite3

import pytest

from db import TradingDB
from trading_costs import round_trip_cost_pct


# --------------------------------------------------------------- pure helper
def test_net_pnl_deducts_modelled_cost():
    gross, notional = 28.90, 1444.85
    net = TradingDB._net_pnl(gross, notional, "SELL", overnight=False)
    expected = round(gross - round_trip_cost_pct(notional, overnight=False, market="IN") * notional, 2)
    assert net == expected
    assert net < gross, "net must be below gross by the trading cost"


def test_net_pnl_only_for_sells():
    assert TradingDB._net_pnl(0.0, 1000.0, "BUY", False) is None
    assert TradingDB._net_pnl(None, 1000.0, "SELL", False) is None
    assert TradingDB._net_pnl(5.0, 0.0, "SELL", False) is None


def test_net_pnl_overnight_costs_more_than_intraday():
    """Delivery (STT both legs + DP charge) must exceed the intraday schedule."""
    intraday = TradingDB._net_pnl(50.0, 2000.0, "SELL", overnight=False)
    overnight = TradingDB._net_pnl(50.0, 2000.0, "SELL", overnight=True)
    assert overnight < intraday


# --------------------------------------------------------------- insert path
def test_insert_sell_stores_net(tmp_path):
    db = TradingDB(db_path=str(tmp_path / "trading_IN.db"))
    rid = db.insert_trade("2026-07-24", "13:21", "GANDHAR.NS", "SELL",
                          7, 290.5, 2033.5, pnl=-11.14, exit_reason="STOP_LOSS")
    row = _row(db, rid)
    assert row["pnl"] == -11.14                     # gross preserved
    assert row["pnl_net"] is not None
    assert row["pnl_net"] < -11.14                  # net worse after costs


def test_insert_buy_has_no_net(tmp_path):
    db = TradingDB(db_path=str(tmp_path / "trading_IN.db"))
    rid = db.insert_trade("2026-07-24", "09:24", "GANDHAR.NS", "BUY",
                          5, 283.19, 1415.95, pnl=0.0, exit_reason="BUY")
    assert _row(db, rid)["pnl_net"] is None


def test_gross_winner_can_become_net_loser(tmp_path):
    """A tiny gross win smaller than costs is really a loss — the exact effect
    that inflated the win rate."""
    db = TradingDB(db_path=str(tmp_path / "trading_IN.db"))
    # +₹2 gross on a ₹2000 position; round-trip cost ~₹6 -> net negative.
    rid = db.insert_trade("2026-07-24", "10:00", "X.NS", "SELL",
                          10, 200.0, 2000.0, pnl=2.0, exit_reason="TAKE_PROFIT")
    row = _row(db, rid)
    assert row["pnl"] > 0 and row["pnl_net"] < 0


# --------------------------------------------------------------- migration
def test_migration_backfills_existing_gross_rows(tmp_path):
    """An OLD db without pnl_net gets the column added and historical SELLs
    backfilled on first open."""
    path = tmp_path / "trading_IN.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, time TEXT,"
        " symbol TEXT, action TEXT, quantity REAL, price REAL, notional REAL, pnl REAL,"
        " exit_reason TEXT, mode TEXT DEFAULT 'paper',"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    conn.execute("INSERT INTO trades(date,time,symbol,action,quantity,price,notional,pnl,exit_reason)"
                 " VALUES('2026-07-20','13:00','A.NS','SELL',5,300,1500,25.4,'TRAILING_STOP')")
    conn.execute("INSERT INTO trades(date,time,symbol,action,quantity,price,notional,pnl,exit_reason)"
                 " VALUES('2026-07-20','09:00','A.NS','BUY',5,295,1475,0.0,'BUY')")
    conn.commit(); conn.close()

    db = TradingDB(db_path=str(path))            # triggers migration + backfill
    conn = sqlite3.connect(str(path)); conn.row_factory = sqlite3.Row
    sell = conn.execute("SELECT * FROM trades WHERE action='SELL'").fetchone()
    buy = conn.execute("SELECT * FROM trades WHERE action='BUY'").fetchone()
    conn.close()
    assert sell["pnl_net"] is not None and sell["pnl_net"] < sell["pnl"]
    assert buy["pnl_net"] is None


def test_migration_is_idempotent(tmp_path):
    """Opening an already-migrated db twice must not double-deduct or error."""
    path = str(tmp_path / "trading_IN.db")
    db1 = TradingDB(db_path=path)
    rid = db1.insert_trade("2026-07-24", "10:00", "A.NS", "SELL",
                           5, 300, 1500, pnl=25.4, exit_reason="TRAILING_STOP")
    net_first = _row(db1, rid)["pnl_net"]
    TradingDB(db_path=path)                       # reopen — migration re-runs
    assert _row(db1, rid)["pnl_net"] == net_first


def _row(db: TradingDB, rid: int):
    with db._conn() as c:
        c.row_factory = sqlite3.Row
        return c.execute("SELECT * FROM trades WHERE id = ?", (rid,)).fetchone()
