"""Daily price history (INR) and the GBP/INR rate from yfinance."""

from __future__ import annotations

import logging
from typing import Dict, Iterable, Optional

import pandas as pd
import yfinance as yf

from . import universe as U

logger = logging.getLogger(__name__)


def history(tickers: Iterable[str], period: str = "2y") -> pd.DataFrame:
    """Adjusted daily closes: index = date, one column per ticker."""
    tickers = list(dict.fromkeys(tickers))
    raw = yf.download(tickers, period=period, interval="1d", auto_adjust=True,
                      progress=False, group_by="column", threads=True)
    if raw is None or raw.empty:
        return pd.DataFrame()
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].rename(
        columns={"Close": tickers[0]})
    closes = closes.dropna(how="all")
    closes.index = pd.to_datetime(closes.index).tz_localize(None)
    return closes


def latest_prices(closes: pd.DataFrame) -> Dict[str, float]:
    last = closes.ffill().iloc[-1]
    return {t: float(v) for t, v in last.items() if pd.notna(v) and v > 0}


def fx_rate(closes: pd.DataFrame, on: Optional[pd.Timestamp] = None) -> float:
    """INR per GBP (latest, or on/before *on*); 0 if unknown."""
    if U.FX not in closes:
        return 0.0
    s = closes[U.FX].dropna()
    if on is not None:
        s = s[s.index <= on]
    return float(s.iloc[-1]) if not s.empty else 0.0


def dividends_per_share(ticker: str, since: Optional[pd.Timestamp] = None) -> pd.Series:
    try:
        s = yf.Ticker(ticker).dividends
    except Exception:
        return pd.Series(dtype=float)
    if s is None or s.empty:
        return pd.Series(dtype=float)
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s[s.index >= since] if since is not None else s
