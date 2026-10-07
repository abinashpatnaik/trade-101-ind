"""Investable universe: NSE large caps by sector, plus optional ETFs.

Tickers are Yahoo symbols; the Zerodha trading symbol is the part before
".NS". The bot ranks the pool by momentum/ML/news and holds the best N, at
most IN_MAX_PER_SECTOR from any one sector. Names are skipped at runtime if
they have no price data or can't be traded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .config import settings


@dataclass(frozen=True)
class Asset:
    ticker: str
    name: str
    kind: str    # stock | etf | benchmark
    group: str   # sector; at most IN_MAX_PER_SECTOR holdings per group

    @property
    def symbol(self) -> str:
        """Zerodha trading symbol."""
        return self.ticker.rsplit(".", 1)[0]


STOCKS: List[Asset] = [
    Asset("HDFCBANK.NS", "HDFC Bank", "stock", "banks"),
    Asset("ICICIBANK.NS", "ICICI Bank", "stock", "banks"),
    Asset("KOTAKBANK.NS", "Kotak Mahindra Bank", "stock", "banks"),
    Asset("AXISBANK.NS", "Axis Bank", "stock", "banks"),
    Asset("SBIN.NS", "State Bank of India", "stock", "banks"),
    Asset("BAJFINANCE.NS", "Bajaj Finance", "stock", "finance"),
    Asset("BAJAJFINSV.NS", "Bajaj Finserv", "stock", "finance"),
    Asset("TCS.NS", "Tata Consultancy Services", "stock", "it"),
    Asset("INFY.NS", "Infosys", "stock", "it"),
    Asset("HCLTECH.NS", "HCL Technologies", "stock", "it"),
    Asset("PERSISTENT.NS", "Persistent Systems", "stock", "it"),
    Asset("RELIANCE.NS", "Reliance Industries", "stock", "energy"),
    Asset("NTPC.NS", "NTPC", "stock", "energy"),
    Asset("POWERGRID.NS", "Power Grid Corporation", "stock", "energy"),
    Asset("BHARTIARTL.NS", "Bharti Airtel", "stock", "telecom"),
    Asset("TITAN.NS", "Titan Company", "stock", "consumer"),
    Asset("TRENT.NS", "Trent", "stock", "consumer"),
    Asset("HINDUNILVR.NS", "Hindustan Unilever", "stock", "consumer"),
    Asset("ASIANPAINT.NS", "Asian Paints", "stock", "consumer"),
    Asset("ETERNAL.NS", "Eternal (Zomato)", "stock", "consumer"),
    Asset("LT.NS", "Larsen & Toubro", "stock", "industrials"),
    Asset("HAL.NS", "Hindustan Aeronautics", "stock", "industrials"),
    Asset("BEL.NS", "Bharat Electronics", "stock", "industrials"),
    Asset("MARUTI.NS", "Maruti Suzuki", "stock", "autos"),
    Asset("M&M.NS", "Mahindra & Mahindra", "stock", "autos"),
    Asset("EICHERMOT.NS", "Eicher Motors", "stock", "autos"),
    Asset("BAJAJ-AUTO.NS", "Bajaj Auto", "stock", "autos"),
    Asset("SUNPHARMA.NS", "Sun Pharmaceutical", "stock", "pharma"),
    Asset("CIPLA.NS", "Cipla", "stock", "pharma"),
    Asset("DIVISLAB.NS", "Divi's Laboratories", "stock", "pharma"),
    Asset("ULTRACEMCO.NS", "UltraTech Cement", "stock", "materials"),
]

# Opt-in only (IN_INCLUDE_ETFS=true): see config.include_etfs for the UK tax catch.
ETFS: List[Asset] = [
    Asset("NIFTYBEES.NS", "Nippon India ETF Nifty 50", "etf", "etf_large"),
    Asset("JUNIORBEES.NS", "Nippon India ETF Nifty Next 50", "etf", "etf_next50"),
    Asset("MID150BEES.NS", "Nippon India ETF Nifty Midcap 150", "etf", "etf_mid"),
    Asset("MON100.NS", "Motilal Oswal Nasdaq 100 ETF", "etf", "etf_us"),
]

# Regime filter and benchmark: never bought.
REGIME_INDEX = "^NSEI"
BENCHMARK = "NIFTYBEES.NS"
FX = "GBPINR=X"
_MARKERS = [Asset(REGIME_INDEX, "Nifty 50 index", "benchmark", "index")]


def pool() -> List[Asset]:
    return STOCKS + (ETFS if settings.include_etfs else [])


ALL = {a.ticker: a for a in STOCKS + ETFS + _MARKERS}


def get(ticker: str) -> Asset:
    return ALL[ticker]


def data_tickers() -> List[str]:
    """Everything price history is needed for (incl. benchmark and FX)."""
    return list(dict.fromkeys([a.ticker for a in pool()] + [REGIME_INDEX, BENCHMARK, FX]))
