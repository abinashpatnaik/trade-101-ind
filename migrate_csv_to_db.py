#!/usr/bin/env python3
"""
migrate_csv_to_db.py
====================
One-time migration script to import existing trades CSV into SQLite.

Usage:
  python migrate_csv_to_db.py                     # auto-detect CSV path
  python migrate_csv_to_db.py /path/to/trades.csv  # explicit path

After successful import, the original CSV is renamed to .bak.
"""

import csv
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def find_csv_path() -> str:
    """Auto-detect the trades CSV path."""
    candidates = [
        "/app/data/trades_US.csv",     # Docker
        "/app/data/trades_IN.csv",     # Docker IN market
        os.path.join(os.path.dirname(__file__), "data", "trades_US.csv"),
        os.path.join(os.path.dirname(__file__), "data", "trades_IN.csv"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""


def migrate(csv_path: str) -> None:
    """Import trades from CSV into SQLite."""
    from db import TradingDB

    if not os.path.exists(csv_path):
        logger.error("CSV file not found: %s", csv_path)
        return

    db = TradingDB()
    logger.info("Database: %s", db.db_path)
    logger.info("Importing from: %s", csv_path)

    imported = 0
    skipped = 0

    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                # Determine mode: if the row has a 'mode' column use it,
                # otherwise tag as 'paper' (legacy data)
                mode = row.get("mode", "").strip() or "paper"

                pnl_str = row.get("pnl", "").strip()
                pnl = float(pnl_str) if pnl_str else None

                db.insert_trade(
                    date=row["date"],
                    time=row.get("time", "00:00:00"),
                    symbol=row["symbol"],
                    action=row["action"],
                    quantity=float(row.get("quantity", 0)),
                    price=float(row.get("price", 0)),
                    notional=float(row.get("notional", 0)),
                    pnl=pnl,
                    exit_reason=row.get("exit_reason", ""),
                    mode=mode,
                )
                imported += 1
            except Exception as e:
                logger.warning("Skipped row: %s — %s", row, e)
                skipped += 1

    logger.info("✅ Imported %d trades (%d skipped)", imported, skipped)

    # Rename original CSV to .bak
    bak_path = csv_path + ".bak"
    os.rename(csv_path, bak_path)
    logger.info("📦 Original CSV backed up to: %s", bak_path)

    # Also migrate ml_validation.json if it exists
    ml_json_path = os.path.join(os.path.dirname(csv_path), "ml_validation.json")
    if os.path.exists(ml_json_path):
        import json
        try:
            with open(ml_json_path, "r") as f:
                logs = json.load(f)
            ml_imported = 0
            for entry in logs:
                db.insert_ml_validation(
                    timestamp=entry.get("timestamp", ""),
                    symbol=entry.get("symbol", ""),
                    action=entry.get("action", ""),
                    approved=entry.get("approved", True),
                    reason=entry.get("reason", ""),
                )
                ml_imported += 1
            ml_bak_path = ml_json_path + ".bak"
            os.rename(ml_json_path, ml_bak_path)
            logger.info("✅ Imported %d ML validation logs, backed up to: %s", ml_imported, ml_bak_path)
        except Exception as e:
            logger.warning("Could not migrate ML validation logs: %s", e)

    logger.info("🎉 Migration complete! You can now delete the .bak files when ready.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = find_csv_path()

    if not path:
        logger.error("No trades CSV found. Pass the path as an argument.")
        sys.exit(1)

    migrate(path)
