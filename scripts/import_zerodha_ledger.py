#!/usr/bin/env python3
"""
import_zerodha_ledger.py
========================
Import a Zerodha fund-ledger export (Console -> Reports -> Ledger, .xlsx) into
the ``account_charges`` table.

WHY THIS EXISTS
---------------
The bot's own books only ever see trade settlements. The fund ledger contains
three other kinds of line that also move real money, and getting any of them
wrong makes the dashboard disagree with the bank:

  funding    UPI deposits. Capital, not performance. NAV rises; return must
             not. Two ₹10,000 deposits in this period.
  overhead   Money genuinely lost that NO trade caused — Kite Connect API
             ₹500.00, DDPI enabling ₹118.00. Real, but it must not be
             amortised into per-trade P&L or it corrupts win rate and
             expectancy for trades that never incurred it.
  transient  Posted then reversed — provisional TDS of ₹1,645.7678 blocked on
             17 Jul and reversed on 20 Jul. Reading either leg alone invents a
             loss (or a gain) that never happened. This is the trap a naive
             NAV-delta reader falls into.

DP charges are deliberately NOT imported here. They are attributable to a
specific delivery sell, so they belong to that trade's cost (trading_costs.
IN_DP_CHARGE, measured at ₹15.34 from this very ledger), not to the account.
Importing them here as well would double-count them.

Settlement vouchers are skipped too: those ARE the trade P&L the bot already
records, and re-importing them would double-count the entire book.

VERIFIED IDENTITY (15 Jun - 29 Jul 2026)
----------------------------------------
    NAV_close - net_funding = gross_trade_pnl - trade_charges - overheads
    17,357.5374 - 20,000    = -457.36 - 1,505.7423 - 679.3602
              -2,642.4626   = -2,642.4625              (0.01 paisa rounding)

Usage:
    python -m scripts.import_zerodha_ledger ledger-YIX026.xlsx [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import TradingDB  # noqa: E402

# Matched against the lowercased "Particulars" text, in order. First hit wins.
FUNDING_HINTS = ("funds added", "funds withdrawn", "bank receipts",
                 "payment towards", "funds transferred")
TRANSIENT_HINTS = ("provisional tds",)
OVERHEAD_HINTS = ("kite connect", "api charges", "ddpi", "call & trade",
                  "account opening", "amc", "annual maintenance",
                  "auto square", "sms charges", "delayed payment")
# Handled elsewhere — see module docstring.
SKIP_HINTS = ("net settlement", "opening balance", "closing balance",
              "dp charges")


def classify(particulars: str) -> str | None:
    """Return the account_charges kind, or None if the line is handled
    elsewhere and must not be imported."""
    text = (particulars or "").strip().lower()
    if not text:
        return None
    for hint in SKIP_HINTS:
        if hint in text:
            return None
    for hint in TRANSIENT_HINTS:
        if hint in text:
            return "transient"
    for hint in FUNDING_HINTS:
        if hint in text:
            return "funding"
    for hint in OVERHEAD_HINTS:
        if hint in text:
            return "overhead"
    # Unrecognised debits are treated as overheads: under-counting a real cost
    # flatters the strategy, which is the more dangerous direction to be wrong.
    return "overhead"


def parse_rows(path: str):
    """Yield (posting_date, particulars, signed_amount) from the export."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    for ws in wb:
        header_seen = False
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else c for c in row]
            texts = [str(c).strip() for c in cells]
            if not header_seen:
                header_seen = any(t == "Particulars" for t in texts)
                continue
            particulars = texts[1] if len(texts) > 1 else ""
            date = texts[2] if len(texts) > 2 else ""
            if not particulars or not date:
                continue

            def _num(idx):
                try:
                    return float(cells[idx] or 0.0)
                except (TypeError, ValueError):
                    return 0.0

            debit, credit = _num(5), _num(6)
            if debit == 0.0 and credit == 0.0:
                continue
            yield date[:10], particulars, credit - debit


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ledger", help="Zerodha ledger .xlsx export")
    ap.add_argument("--mode", default="live")
    ap.add_argument("--dry-run", action="store_true",
                    help="classify and print, write nothing")
    args = ap.parse_args()

    db = None if args.dry_run else TradingDB()
    totals = {"funding": 0.0, "overhead": 0.0, "transient": 0.0}
    imported = skipped = 0

    for date, particulars, amount in parse_rows(args.ledger):
        kind = classify(particulars)
        if kind is None:
            skipped += 1
            continue
        totals[kind] += amount
        imported += 1
        print(f"  {date}  {kind:9s} {amount:>12,.4f}  {particulars[:62]}")
        if db is not None:
            db.record_account_charge(date, particulars, amount, kind, args.mode)

    print(f"\n{imported} imported, {skipped} skipped "
          f"(settlements/DP charges/balances are handled elsewhere)")
    for kind, total in totals.items():
        print(f"  {kind:9s} {total:>12,.2f}")
    if abs(totals["transient"]) > 0.01:
        print(f"\n  WARNING: transient lines do not net to zero "
              f"({totals['transient']:,.2f}) — an unreversed provisional "
              f"entry, or the export window cuts one in half.")
    if args.dry_run:
        print("\n(dry run — nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
