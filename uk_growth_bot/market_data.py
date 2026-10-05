"""Daily price history from yfinance, normalised to GBP.

Yahoo quotes most London lines in pence (currency "GBp"); everything here is
converted to pounds so sizing and tax maths never mix units.
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_currency_cache: Dict[str, str] = {}


def _currency(ticker: str) -> str:
    if ticker not in _currency_cache:
        try:
            _currency_cache[ticker] = str(yf.Ticker(ticker).fast_info["currency"])
        except Exception as exc:
            logger.warning("Currency lookup failed for %s (%s); assuming GBp", ticker, exc)
            _currency_cache[ticker] = "GBp"
    return _currency_cache[ticker]


def _to_gbp_factor(ticker: str) -> float:
    cur = _currency(ticker)
    if cur in ("GBp", "GBX"):
        return 0.01
    if cur != "GBP":
        logger.warning("%s is quoted in %s, not GBP — check the listing", ticker, cur)
    return 1.0


def history(tickers: Iterable[str], period: str = "6y") -> pd.DataFrame:
    """Adjusted daily closes in GBP: index = date, one column per ticker."""
    tickers = list(dict.fromkeys(tickers))
    raw = yf.download(tickers, period=period, interval="1d", auto_adjust=True,
                      progress=False, group_by="column", threads=True)
    if raw is None or raw.empty:
        return pd.DataFrame()
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].rename(
        columns={"Close": tickers[0]})
    closes = closes.dropna(how="all")
    for t in closes.columns:
        closes[t] = closes[t] * _to_gbp_factor(t)
    closes.index = pd.to_datetime(closes.index).tz_localize(None)
    return closes


def latest_prices(closes: pd.DataFrame) -> Dict[str, float]:
    last = closes.ffill().iloc[-1]
    return {t: float(v) for t, v in last.items() if pd.notna(v) and v > 0}


def dividends_per_share(ticker: str, since: Optional[pd.Timestamp] = None) -> pd.Series:
    try:
        s = yf.Ticker(ticker).dividends
    except Exception:
        return pd.Series(dtype=float)
    if s is None or s.empty:
        return pd.Series(dtype=float)
    s.index = pd.to_datetime(s.index).tz_localize(None)
    s = s * _to_gbp_factor(ticker)
    return s[s.index >= since] if since is not None else s
