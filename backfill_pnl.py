"""
backfill_pnl.py
===============
One-off maintenance tool: correct historical fabricated exit P&L.

Older SELL rows recorded the exit at a live market quote fetched at broker-sync
time instead of the real fill (the bug fixed in the trader/portfolio pipeline).
This script re-derives each historical SELL's execution price from Alpaca's
actual filled order history and recomputes P&L against the matching entry.

SAFETY
------
- Dry-run by DEFAULT: prints proposed changes and writes nothing.
- ``--apply`` is required to modify the DB, and it makes a timestamped backup
  copy of the SQLite file first.
- A SELL row is only corrected when exactly one Alpaca fill matches it by
  symbol + quantity (quantities are near-unique for fractional shares); the
  recorded timestamp only breaks ties. Ambiguous / unmatched rows are skipped
  and reported for manual review — never guessed.

USAGE (run on the server where Alpaca creds + the prod DB live)
--------------------------------------------------------------
    python backfill_pnl.py                    # dry-run, all SELLs
    python backfill_pnl.py --since 2026-07-01 # dry-run, recent only
    python backfill_pnl.py --since 2026-07-01 --apply
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("backfill_pnl")

# Only propose a change when the price moves by at least this much (avoids
# rewriting rows that were already recorded at (near) the real fill).
_PRICE_EPS = 0.005


def parse_trade_dt(date_s: str, time_s: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_s} {time_s}", fmt)
        except (ValueError, TypeError):
            continue
    return None


def _entry_price_for(sell: Dict, buys: List[Dict]) -> Optional[float]:
    """Most recent BUY of the same symbol at/before the sell — the entry cost."""
    sdt = parse_trade_dt(sell["date"], sell["time"])
    best = None
    for b in buys:
        if b["symbol"] != sell["symbol"]:
            continue
        bdt = parse_trade_dt(b["date"], b["time"])
        if sdt and bdt and bdt > sdt:
            continue
        if best is None or (bdt and best[0] and bdt > best[0]):
            best = (bdt, float(b["price"]))
    return best[1] if best else None


def plan_corrections(
    sells: List[Dict],
    buys: List[Dict],
    fills: List[Dict],
    qty_tol: float = 0.02,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Pure matching logic (no I/O) — unit-testable.

    fills: list of {"symbol", "qty", "price", "filled_at": datetime|None}
    Returns (corrections, skipped). Each correction carries the proposed
    new price/pnl and the old values.
    """
    corrections: List[Dict] = []
    skipped: List[Dict] = []

    for s in sells:
        qty = float(s["quantity"])
        candidates = [
            f for f in fills
            if f["symbol"] == s["symbol"]
            and qty > 0
            and abs(float(f["qty"]) - qty) <= qty_tol * qty
        ]
        if not candidates:
            skipped.append({**s, "_why": "no matching Alpaca fill"})
            continue
        if len(candidates) > 1:
            sdt = parse_trade_dt(s["date"], s["time"])
            timed = [c for c in candidates if c.get("filled_at") and sdt]
            if sdt and timed:
                timed.sort(key=lambda c: abs((c["filled_at"] - sdt).total_seconds()))
                # Disambiguate only if the closest is clearly closer than the next.
                if len(timed) == 1 or abs((timed[0]["filled_at"] - sdt).total_seconds()) + 1 < \
                        abs((timed[1]["filled_at"] - sdt).total_seconds()):
                    match = timed[0]
                else:
                    skipped.append({**s, "_why": f"{len(candidates)} ambiguous fills"})
                    continue
            else:
                skipped.append({**s, "_why": f"{len(candidates)} ambiguous fills"})
                continue
        else:
            match = candidates[0]

        new_price = float(match["price"])
        old_price = float(s["price"])
        if abs(new_price - old_price) < _PRICE_EPS:
            continue  # already correct

        entry = _entry_price_for(s, buys)
        new_pnl = (new_price - entry) * qty if entry is not None else None
        corrections.append({
            "id": s["id"], "symbol": s["symbol"], "date": s["date"], "time": s["time"],
            "exit_reason": s.get("exit_reason"),
            "old_price": old_price, "new_price": new_price,
            "old_pnl": s.get("pnl"), "new_pnl": new_pnl,
            "entry_price": entry,
        })
    return corrections, skipped


def fetch_alpaca_sell_fills(connector, since: Optional[str]) -> List[Dict]:
    """Pull all filled SELL orders from Alpaca (paginated) as match candidates."""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus, OrderSide

    client = connector.trading_client
    fills: List[Dict] = []
    after = None
    if since:
        after = datetime.strptime(since, "%Y-%m-%d")
    while True:
        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=500,
                               side=OrderSide.SELL, after=after, nested=False)
        batch = client.get_orders(filter=req)
        if not batch:
            break
        for o in batch:
            fill = getattr(o, "filled_avg_price", None)
            qty = getattr(o, "filled_qty", None)
            if fill is None or float(fill) <= 0 or qty is None or float(qty) <= 0:
                continue
            fills.append({
                "symbol": o.symbol,
                "qty": float(qty),
                "price": float(fill),
                "filled_at": getattr(o, "filled_at", None),
            })
        if len(batch) < 500:
            break
        after = getattr(batch[-1], "submitted_at", None) or after
        if after is None:
            break
    return fills


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Correct historical fabricated exit P&L from Alpaca fills.")
    ap.add_argument("--since", help="Only process SELLs on/after this date (YYYY-MM-DD).")
    ap.add_argument("--qty-tol", type=float, default=0.02, help="Quantity match tolerance (fraction).")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run).")
    args = ap.parse_args()

    from alpaca_connector import AlpacaConnector
    from db import TradingDB

    connector = AlpacaConnector()
    connector.connect()
    db = TradingDB()

    all_trades = db.get_trades(limit=100000)
    sells = [t for t in all_trades if t["action"] == "SELL" and (not args.since or t["date"] >= args.since)]
    buys = [t for t in all_trades if t["action"] == "BUY"]
    logger.info("Loaded %d SELL rows (of %d trades) from %s", len(sells), len(all_trades), db.db_path)

    fills = fetch_alpaca_sell_fills(connector, args.since)
    logger.info("Fetched %d filled SELL orders from Alpaca.", len(fills))

    corrections, skipped = plan_corrections(sells, buys, fills, qty_tol=args.qty_tol)

    if not corrections:
        logger.info("No corrections needed — recorded exits already match real fills.")
    else:
        logger.info("\nProposed corrections (%d):", len(corrections))
        logger.info("%-6s %-6s %-16s %-12s  price: %10s -> %-10s   pnl: %8s -> %-8s",
                    "id", "sym", "when", "reason", "old", "new", "old", "new")
        for c in corrections:
            logger.info(
                "%-6s %-6s %-16s %-12s  price: %10.4f -> %-10.4f   pnl: %8s -> %-8s",
                c["id"], c["symbol"], f'{c["date"]} {c["time"]}', (c["exit_reason"] or "-")[:12],
                c["old_price"], c["new_price"],
                f'{c["old_pnl"]:.2f}' if c["old_pnl"] is not None else "None",
                f'{c["new_pnl"]:.2f}' if c["new_pnl"] is not None else "None",
            )

    if skipped:
        logger.info("\nSkipped (need manual review) — %d:", len(skipped))
        for s in skipped:
            logger.info("  id=%s %s %s %s: %s", s["id"], s["symbol"], s["date"], s["time"], s["_why"])

    if not args.apply:
        logger.info("\nDRY-RUN — nothing written. Re-run with --apply to commit the corrections above.")
        return

    if not corrections:
        return
    backup = f"{db.db_path}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(db.db_path, backup)
    logger.info("\nDB backed up to %s", backup)
    for c in corrections:
        db.update_trade_price_pnl(c["id"], c["new_price"], c["new_pnl"])
    logger.info("Applied %d corrections.", len(corrections))


if __name__ == "__main__":
    main()
