"""
price_feed.py
=============
Market-data provider that wraps yfinance for OHLCV history and real-time
price retrieval.  All LSE symbols are appended with '.L' as required by the
Yahoo Finance API.

Provides:
  - Historical OHLCV at configurable periods and intervals
  - Intraday 1-minute data
  - VWAP calculation
  - Current price (latest close of most-recent bar)

Requires:
    pip install yfinance>=0.2.36 pandas>=2.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

import os
import datetime
import pandas as pd
import yfinance as yf

from config import config

ACTIVE_MARKET = os.getenv("TRADING_MARKET", "IN").upper()

if ACTIVE_MARKET == "US":
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

logger = logging.getLogger(__name__)


class PriceFeed:
    """
    Retrieves OHLCV price data from Yahoo Finance for LSE-listed equities.

    All methods accept the bare IBKR-format ticker (e.g. ``'HSBA'``) and
    internally append ``.L`` for the Yahoo Finance lookup.

    Usage
    -----
    >>> feed = PriceFeed()
    >>> df = feed.get_ohlcv('HSBA', period='5d', interval='5m')
    >>> price = feed.get_current_price('AZN')
    """

    # Columns guaranteed to exist in every returned DataFrame.
    REQUIRED_COLS = {"Open", "High", "Low", "Close", "Volume"}

    def __init__(self) -> None:
        logger.debug("PriceFeed initialised.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _yahoo_symbol(symbol: str) -> str:
        """Convert a symbol to its Yahoo Finance equivalent."""
        symbol = symbol.strip().upper()
        if not (symbol.endswith(".NS") or symbol.endswith(".BO")):
            return symbol + ".NS"
        return symbol

    def _fetch(
        self,
        symbol: str,
        period: Optional[str] = None,
        interval: str = "5m",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        """
        Core wrapper for data fetch. Routes to Alpaca or yfinance based on market.
        """
        if ACTIVE_MARKET == "US":
            return self._fetch_alpaca(symbol, period, interval, start, end)
        else:
            return self._fetch_yfinance(symbol, period, interval, start, end)

    def _fetch_alpaca(
        self,
        symbol: str,
        period: Optional[str] = None,
        interval: str = "5m",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        try:
            if not getattr(self, "alpaca_client", None):
                if not config.alpaca.api_key or not config.alpaca.api_secret:
                    logger.error("Alpaca API keys not configured. Cannot fetch data.")
                    return None
                self.alpaca_client = StockHistoricalDataClient(config.alpaca.api_key, config.alpaca.api_secret)

            # Map interval to timeframe
            if interval == "1m":
                tf = TimeFrame.Minute
            elif interval == "5m":
                tf = TimeFrame(5, TimeFrameUnit.Minute)
            elif interval == "15m":
                tf = TimeFrame(15, TimeFrameUnit.Minute)
            elif interval in ("1h", "60m"):
                tf = TimeFrame.Hour
            elif interval == "1d":
                tf = TimeFrame.Day
            else:
                tf = TimeFrame.Minute

            # Compute start and end
            now = datetime.datetime.now(datetime.timezone.utc)
            if start is not None and end is not None:
                start_dt = pd.to_datetime(start).to_pydatetime()
                end_dt = pd.to_datetime(end).to_pydatetime()
            else:
                days = 1
                if period:
                    if period.endswith('d'):
                        days = int(period[:-1])
                    elif period.endswith('mo'):
                        days = int(period[:-2]) * 30
                    elif period.endswith('y'):
                        days = int(period[:-1]) * 365
                start_dt = now - datetime.timedelta(days=days)
                end_dt = now

            req = StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=tf,
                start=start_dt,
                end=end_dt,
                feed="iex" if config.alpaca.paper_mode else "sip"
            )
            bars = self.alpaca_client.get_stock_bars(req)
            if not bars.data or symbol not in bars.data:
                logger.warning("No data returned from Alpaca for %s", symbol)
                return None
            
            df = bars.df
            if df.empty:
                return None

            # Alpaca returns MultiIndex (symbol, timestamp). Reset it to DatetimeIndex.
            df = df.reset_index(level=0, drop=True)
            
            # Rename columns to match agent's expected Title Case OHLCV
            df.rename(columns={
                'open': 'Open',
                'high': 'High',
                'low': 'Low',
                'close': 'Close',
                'volume': 'Volume'
            }, inplace=True)
            
            df = df[["Open", "High", "Low", "Close", "Volume"]]
            logger.debug("Fetched %d bars for %s from Alpaca (interval=%s)", len(df), symbol, interval)
            return df
            
        except Exception as exc:
            logger.error("Alpaca download failed for %s: %s", symbol, exc, exc_info=True)
            return None

    def _fetch_yfinance(
        self,
        symbol: str,
        period: Optional[str] = None,
        interval: str = "5m",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        """
        Core yfinance download wrapper.

        Returns a cleaned DataFrame or None on failure.
        Uses ``period`` for relative lookbacks and ``start``/``end`` for
        absolute windows (passing both period and start/end is invalid in
        yfinance; this method enforces that).
        """
        ysym = self._yahoo_symbol(symbol)
        try:
            kwargs: dict = {"interval": interval, "auto_adjust": True, "progress": False}
            if start is not None:
                kwargs["start"] = start
                if end is not None:
                    kwargs["end"] = end
            elif period is not None:
                kwargs["period"] = period
            else:
                kwargs["period"] = "1d"

            df: pd.DataFrame = yf.download(ysym, **kwargs)

            if df is None or df.empty:
                logger.warning("No data returned from yfinance for %s", ysym)
                return None

            # yfinance sometimes returns MultiIndex columns — flatten them.
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]

            # Normalise column names to title-case.
            df.columns = [c.title() for c in df.columns]

            # Keep only OHLCV columns to avoid surprises from yfinance extras.
            existing_required = self.REQUIRED_COLS & set(df.columns)
            if len(existing_required) < len(self.REQUIRED_COLS):
                missing = self.REQUIRED_COLS - existing_required
                logger.warning("yfinance data for %s missing columns: %s", ysym, missing)

            df = df[[c for c in df.columns if c in self.REQUIRED_COLS | {"Dividends", "Stock Splits"}]]
            # Drop non-OHLCV extras (dividends etc.)
            df = df[[c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]]

            # Drop rows with NaN in Close.
            df.dropna(subset=["Close"], inplace=True)

            logger.debug(
                "Fetched %d bars for %s (interval=%s)", len(df), ysym, interval
            )
            return df

        except Exception as exc:
            logger.error("yfinance download failed for %s: %s", ysym, exc, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_ohlcv(
        self,
        symbol: str,
        period: str = "5d",
        interval: str = "5m",
    ) -> Optional[pd.DataFrame]:
        """
        Return OHLCV data for *symbol*.

        Parameters
        ----------
        symbol:
            Bare LSE ticker, e.g. ``'HSBA'``.
        period:
            Lookback period string accepted by yfinance: ``'1d'``, ``'5d'``,
            ``'1mo'``, ``'3mo'``, ``'6mo'``, ``'1y'``, ``'2y'``, etc.
        interval:
            Bar interval: ``'1m'``, ``'2m'``, ``'5m'``, ``'15m'``,
            ``'30m'``, ``'60m'``, ``'90m'``, ``'1h'``, ``'1d'``, ``'1wk'``.

        Returns
        -------
        pd.DataFrame or None
            Columns: Open, High, Low, Close, Volume.  Index: DatetimeIndex.
        """
        return self._fetch(symbol, period=period, interval=interval)

    def get_intraday_data(
        self,
        symbol: str,
        days: int = 1,
    ) -> Optional[pd.DataFrame]:
        """
        Return 1-minute intraday OHLCV data for *symbol*.

        Yahoo Finance's free tier provides up to 7 days of 1-minute data.

        Parameters
        ----------
        symbol:
            Bare LSE ticker.
        days:
            Number of calendar days to look back (1–7).

        Returns
        -------
        pd.DataFrame or None
        """
        days = max(1, min(days, 7))
        period = f"{days}d"
        return self._fetch(symbol, period=period, interval="1m")

    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Return the most-recent close price for *symbol*.

        Uses a 1-day 1-minute bar request and returns the last ``Close`` value.
        Falls back to a 5-day 5-minute bar if the 1-minute fetch fails.

        Returns
        -------
        float or None
        """
        df = self.get_intraday_data(symbol, days=1)
        if df is not None and not df.empty and "Close" in df.columns:
            price = float(df["Close"].iloc[-1])
            logger.debug("Current price for %s: %.4f", symbol, price)
            return price

        # Fallback: use 5-minute data.
        df_fallback = self._fetch(symbol, period="1d", interval="5m")
        if df_fallback is not None and not df_fallback.empty and "Close" in df_fallback.columns:
            price = float(df_fallback["Close"].iloc[-1])
            logger.debug("Fallback price for %s: %.4f", symbol, price)
            return price

        logger.error("Could not determine current price for %s", symbol)
        return None

    @staticmethod
    def calculate_vwap(df: pd.DataFrame) -> Optional[pd.Series]:
        """
        Calculate the Volume Weighted Average Price (VWAP) for each bar in *df*.

        VWAP = cumulative(typical_price × volume) / cumulative(volume)

        where ``typical_price = (High + Low + Close) / 3``.

        Parameters
        ----------
        df:
            DataFrame with columns Open, High, Low, Close, Volume.

        Returns
        -------
        pd.Series or None
            VWAP series indexed like *df*, or None if required columns are missing.
        """
        required = {"High", "Low", "Close", "Volume"}
        if not required.issubset(df.columns):
            logger.warning("VWAP calculation requires %s columns.", required)
            return None

        try:
            typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
            cum_tp_vol = (typical_price * df["Volume"]).cumsum()
            cum_vol = df["Volume"].cumsum()

            # Avoid division by zero on bars with zero volume.
            vwap = cum_tp_vol / cum_vol.replace(0, float("nan"))
            vwap.name = "VWAP"
            return vwap

        except Exception as exc:
            logger.error("VWAP calculation failed: %s", exc, exc_info=True)
            return None

    def get_daily_ohlcv(
        self,
        symbol: str,
        period: str = "3mo",
    ) -> Optional[pd.DataFrame]:
        """
        Return daily OHLCV bars.  Useful for long-lookback indicators
        such as a 200-day moving average or monthly trend analysis.

        Parameters
        ----------
        symbol:
            Bare LSE ticker.
        period:
            yfinance period string (e.g. ``'3mo'``, ``'1y'``).

        Returns
        -------
        pd.DataFrame or None
        """
        return self._fetch(symbol, period=period, interval="1d")
