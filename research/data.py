"""
research.data
=============
Market-aware bar access for offline research, with an on-disk cache.

The two markets have very different history depth, and that asymmetry drove
every conclusion in this project:

- **US (Alpaca)** — 5m bars back 5+ years. Enough for many disjoint windows
  across bull, bear and chop. This is where a hypothesis can actually be
  falsified.
- **IN (yfinance)** — 5m bars only ~60 days, so at most ~4 disjoint 10-day
  windows inside a single regime. Results there are directional at best; the
  harness warns rather than pretending otherwise.

Daily bars (used for point-in-time ranking) go back years on both.

Nothing here is imported by the live trading path.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_CACHE = os.getenv("RESEARCH_CACHE", "/tmp/research_cache")

# yfinance refuses intraday requests older than this.
YF_INTRADAY_LIMIT_DAYS = 58
# Regular US session in UTC (Alpaca returns extended hours too; the live
# system does not trade them, so including them would test a different system).
US_RTH_START_MIN = 13 * 60 + 30
US_RTH_END_MIN = 20 * 60


class BarSource:
    """Fetches OHLCV bars for one market, caching to disk."""

    def __init__(self, market: str = "IN", cache_dir: str = DEFAULT_CACHE) -> None:
        self.market = market.upper()
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self._alpaca = None

    # ---------------------------------------------------------------- cache
    def _cache_path(self, kind: str, key: str) -> str:
        safe = key.replace("/", "_").replace(":", "_")
        d = os.path.join(self.cache_dir, self.market, kind)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{safe}.pkl")

    def _cached(self, kind: str, key: str) -> Optional[pd.DataFrame]:
        p = self._cache_path(kind, key)
        if os.path.exists(p):
            try:
                return pd.read_pickle(p)
            except Exception:
                logger.debug("corrupt cache entry %s — refetching", p)
        return None

    def _store(self, kind: str, key: str, df: pd.DataFrame) -> None:
        try:
            pd.to_pickle(df, self._cache_path(kind, key))
        except Exception as exc:
            logger.debug("cache write failed for %s: %s", key, exc)

    # --------------------------------------------------------------- alpaca
    def _alpaca_client(self):
        if self._alpaca is None:
            from alpaca.data.historical import StockHistoricalDataClient
            key = os.getenv("APCA_API_KEY_ID")
            sec = os.getenv("APCA_API_SECRET_KEY")
            if not key or not sec:
                raise RuntimeError(
                    "US research needs APCA_API_KEY_ID / APCA_API_SECRET_KEY "
                    "(the vetting-us image is run with --env-file .env)")
            self._alpaca = StockHistoricalDataClient(key, sec)
        return self._alpaca

    @staticmethod
    def _normalise(df: pd.DataFrame) -> pd.DataFrame:
        return df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                  "close": "Close", "volume": "Volume"})

    # --------------------------------------------------------------- public
    def intraday_limit_days(self) -> Optional[int]:
        """Calendar days of 5m history available, or None when effectively unbounded."""
        return None if self.market == "US" else YF_INTRADAY_LIMIT_DAYS

    def bars_5m(self, symbol: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
        """5-minute bars over ``[start, end)``. Returns None when unavailable."""
        key = f"{symbol}_{start:%Y%m%d}_{end:%Y%m%d}"
        hit = self._cached("m5", key)
        if hit is not None:
            return hit

        if self.market == "US":
            df = self._us_5m(symbol, start, end)
        else:
            df = self._in_5m(symbol, start, end)

        if df is None or df.empty:
            return None
        df = df.dropna(subset=["Close"])
        self._store("m5", key, df)
        return df

    def _us_5m(self, symbol, start, end) -> Optional[pd.DataFrame]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        try:
            req = StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                start=start, end=end)
            df = self._alpaca_client().get_stock_bars(req).df
        except Exception as exc:
            logger.debug("alpaca 5m failed for %s: %s", symbol, exc)
            return None
        if df is None or df.empty:
            return None
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)
        df = self._normalise(df)[["Open", "High", "Low", "Close", "Volume"]]
        idx = df.index.tz_convert("UTC")
        mins = idx.hour * 60 + idx.minute
        return df[(mins >= US_RTH_START_MIN) & (mins < US_RTH_END_MIN)]

    def _in_5m(self, symbol, start, end) -> Optional[pd.DataFrame]:
        import yfinance as yf
        if (datetime.now() - start).days > YF_INTRADAY_LIMIT_DAYS:
            logger.debug("%s: %s is beyond yfinance's intraday window", symbol, start.date())
            return None
        try:
            df = yf.download(symbol, start=start.strftime("%Y-%m-%d"),
                             end=end.strftime("%Y-%m-%d"), interval="5m",
                             progress=False, auto_adjust=False)
        except Exception as exc:
            logger.debug("yfinance 5m failed for %s: %s", symbol, exc)
            return None
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        time.sleep(0.3)      # be polite; yfinance bans aggressive callers
        return df

    def daily(self, symbols: List[str], years: int = 2) -> Dict[str, pd.DataFrame]:
        """Daily bars per symbol — the ranking input for point-in-time selection."""
        start = datetime.utcnow() - timedelta(days=365 * years)
        end = datetime.utcnow() - timedelta(days=1)
        out: Dict[str, pd.DataFrame] = {}
        missing = []
        for s in symbols:
            hit = self._cached("daily", f"{s}_{years}y")
            if hit is not None:
                out[s] = hit
            else:
                missing.append(s)
        if not missing:
            return out

        if self.market == "US":
            fetched = self._us_daily(missing, start, end)
        else:
            fetched = self._in_daily(missing, start, end)
        for s, df in fetched.items():
            self._store("daily", f"{s}_{years}y", df)
            out[s] = df
        return out

    def _us_daily(self, symbols, start, end) -> Dict[str, pd.DataFrame]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        out = {}
        try:
            req = StockBarsRequest(symbol_or_symbols=list(symbols),
                                   timeframe=TimeFrame.Day, start=start, end=end)
            df = self._alpaca_client().get_stock_bars(req).df
        except Exception as exc:
            logger.warning("alpaca daily failed: %s", exc)
            return out
        for s in symbols:
            try:
                out[s] = self._normalise(df.xs(s, level=0))
            except Exception:
                pass
        return out

    def _in_daily(self, symbols, start, end) -> Dict[str, pd.DataFrame]:
        import yfinance as yf
        out = {}
        batch = 100
        for i in range(0, len(symbols), batch):
            chunk = list(symbols)[i:i + batch]
            try:
                data = yf.download(" ".join(chunk), period="2y", interval="1d",
                                   group_by="ticker", threads=True,
                                   progress=False, auto_adjust=False)
            except Exception as exc:
                logger.warning("yfinance daily batch failed: %s", exc)
                continue
            for s in chunk:
                try:
                    d = data[s].dropna(subset=["Close"])
                    if len(d) >= 80:
                        out[s] = d
                except Exception:
                    pass
            time.sleep(1.0)
        return out

    def trading_calendar(self, reference: str, years: int = 5) -> List[pd.Timestamp]:
        """Trading days from a liquid reference symbol's daily bars."""
        daily = self.daily([reference], years=years)
        if reference not in daily:
            raise RuntimeError(f"no daily bars for calendar reference {reference}")
        idx = daily[reference].index
        return [d.normalize() for d in idx]
