"""
db.py
=====
Central SQLite database module for the trading agent.

Manages a single `data/trading.db` file with tables for:
  - trades       — all BUY/SELL executions (tagged paper/live)
  - signals      — latest market signals per symbol
  - ml_validations — ML validator approval/rejection log

Usage
-----
>>> from db import TradingDB
>>> db = TradingDB()          # auto-creates tables
>>> db.insert_trade(...)
>>> trades = db.get_trades(mode="live", symbol="AAPL")
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_IN_DOCKER = os.environ.get("TRADES_CSV_PATH") is not None or os.path.exists("/.dockerenv")
_DEFAULT_DB_PATH = "/app/data/trading.db" if _IN_DOCKER else os.path.join(
    os.path.dirname(__file__), "data", "trading.db"
)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    notional REAL NOT NULL,
    pnl REAL,
    exit_reason TEXT,
    mode TEXT DEFAULT 'paper',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date);
CREATE INDEX IF NOT EXISTS idx_trades_mode ON trades(mode);

CREATE TABLE IF NOT EXISTS nav_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    nav REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nav_history_ts ON nav_history(timestamp);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    price REAL,
    change_pct REAL DEFAULT 0.0,
    rsi REAL,
    trend_score REAL,
    macd_signal TEXT,
    ema_signal TEXT,
    combined_score REAL,
    signal TEXT,
    confidence INTEGER,
    buy_threshold REAL,
    sell_threshold REAL,
    ai_decision TEXT,
    ai_reason TEXT,
    hold_reason TEXT DEFAULT '',
    ml_confidence REAL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ml_validations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 1,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ml_val_symbol ON ml_validations(symbol);
"""


class TradingDB:
    """Thread-safe SQLite wrapper for the trading agent."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or os.getenv("TRADING_DB_PATH", _DEFAULT_DB_PATH)

        # Ensure directory exists
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._init_db()
        logger.info("TradingDB initialised at %s", self._db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Create a new connection (safe for multi-threaded use)."""
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")   # better concurrency
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _conn(self):
        """Context manager for a database connection."""
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        with self._conn() as conn:
            conn.executescript(_SCHEMA_SQL)
            # Migrate: add hold_reason column if missing (existing DBs)
            try:
                conn.execute("ALTER TABLE signals ADD COLUMN hold_reason TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # Column already exists
            # Migrate: add ml_confidence column if missing
            try:
                conn.execute("ALTER TABLE signals ADD COLUMN ml_confidence REAL")
            except sqlite3.OperationalError:
                pass
        logger.debug("Database schema verified.")

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def insert_trade(
        self,
        date: str,
        time: str,
        symbol: str,
        action: str,
        quantity: float,
        price: float,
        notional: float,
        pnl: Optional[float] = None,
        exit_reason: Optional[str] = None,
        mode: str = "paper",
    ) -> int:
        """Insert a trade record. Returns the row ID."""
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO trades
                   (date, time, symbol, action, quantity, price, notional, pnl, exit_reason, mode)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (date, time, symbol, action.upper(), quantity, price,
                 round(notional, 2), round(pnl, 2) if pnl is not None else None,
                 exit_reason or None, mode),
            )
            return cursor.lastrowid

    def get_trades(
        self,
        date: Optional[str] = None,
        symbol: Optional[str] = None,
        mode: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """
        Query trades with optional filters.

        Parameters
        ----------
        date : str, optional
            Filter by date (YYYY-MM-DD).
        symbol : str, optional
            Filter by symbol.
        mode : str, optional
            Filter by mode ('paper' or 'live').
        limit : int
            Max rows to return.
        """
        clauses: List[str] = []
        params: List[Any] = []

        if date:
            clauses.append("date = ?")
            params.append(date)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if mode:
            clauses.append("mode = ?")
            params.append(mode)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM trades {where} ORDER BY date DESC, time DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_trade_summary(
        self,
        symbol: str,
        mode: Optional[str] = None,
        since_date: Optional[str] = None,
    ) -> Dict[str, float]:
        """
        Get aggregated trade stats for a symbol.

        Returns dict with totalBought, totalSold, totalPnl.
        """
        clauses = ["symbol = ?"]
        params: List[Any] = [symbol]

        if mode:
            clauses.append("mode = ?")
            params.append(mode)
        if since_date:
            clauses.append("date >= ?")
            params.append(since_date)

        where = f"WHERE {' AND '.join(clauses)}"

        with self._conn() as conn:
            row = conn.execute(
                f"""SELECT
                    COALESCE(SUM(CASE WHEN action='BUY' THEN notional ELSE 0 END), 0) as total_bought,
                    COALESCE(SUM(CASE WHEN action='SELL' THEN notional ELSE 0 END), 0) as total_sold,
                    COALESCE(SUM(CASE WHEN action='SELL' THEN pnl ELSE 0 END), 0) as total_pnl
                FROM trades {where}""",
                params,
            ).fetchone()
            return {
                "totalBought": row["total_bought"],
                "totalSold": row["total_sold"],
                "totalPnl": row["total_pnl"],
            }

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def upsert_signal(self, signal: Dict[str, Any]) -> None:
        """Insert or update a signal for a symbol."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO signals
                   (symbol, price, change_pct, rsi, trend_score, macd_signal, ema_signal,
                    combined_score, signal, confidence, buy_threshold, sell_threshold,
                    ai_decision, ai_reason, hold_reason, ml_confidence, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(symbol) DO UPDATE SET
                    price=excluded.price, change_pct=excluded.change_pct,
                    rsi=excluded.rsi, trend_score=excluded.trend_score,
                    macd_signal=excluded.macd_signal, ema_signal=excluded.ema_signal,
                    combined_score=excluded.combined_score, signal=excluded.signal,
                    confidence=excluded.confidence, buy_threshold=excluded.buy_threshold,
                    sell_threshold=excluded.sell_threshold, ai_decision=excluded.ai_decision,
                    ai_reason=excluded.ai_reason, hold_reason=excluded.hold_reason,
                    ml_confidence=excluded.ml_confidence,
                    updated_at=CURRENT_TIMESTAMP""",
                (
                    signal["symbol"],
                    signal.get("price", 0.0),
                    signal.get("changePct", 0.0),
                    signal.get("rsi", 0.0),
                    signal.get("trendScore", 0.0),
                    signal.get("macdSignal", "neutral"),
                    signal.get("emaSignal", "neutral"),
                    signal.get("combinedScore", 0.0),
                    signal.get("signal", "HOLD"),
                    signal.get("confidence", 0),
                    signal.get("buyThreshold", 0.0),
                    signal.get("sellThreshold", 0.0),
                    signal.get("aiDecision", ""),
                    signal.get("aiReason", ""),
                    signal.get("holdReason", ""),
                    signal.get("mlConfidence", 0.0),
                ),
            )

    def upsert_signals(self, signals: List[Dict[str, Any]]) -> None:
        """Batch upsert multiple signals."""
        for sig in signals:
            self.upsert_signal(sig)

    def get_signals(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get the latest signals, sorted by absolute trend score."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM signals
                   ORDER BY ABS(trend_score) DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # ML Validations
    # ------------------------------------------------------------------

    def log_ml_validation(
        self,
        symbol: str,
        ml_prediction: str,
        ml_confidence: float,
        model_name: str,
        is_approved: bool,
        reasoning: str,
    ) -> None:
        """Log a validation decision made by the ML layer."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO ml_validations
                (symbol, ml_prediction, ml_confidence, model_name, is_approved, reasoning)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (symbol, ml_prediction, ml_confidence, model_name, int(is_approved), reasoning),
            )
            
    # ------------------------------------------------------------------
    # NAV History
    # ------------------------------------------------------------------

    def insert_nav_record(self, nav: float) -> None:
        """Insert a snapshot of the portfolio's Net Asset Value."""
        from datetime import datetime
        now_iso = datetime.utcnow().isoformat() + "Z"
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO nav_history (timestamp, nav) VALUES (?, ?)",
                (now_iso, nav)
            )
            return cursor.lastrowid

    def get_ml_validations(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent ML validation logs, most recent first."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM ml_validations
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @property
    def db_path(self) -> str:
        """Return the database file path."""
        return self._db_path
