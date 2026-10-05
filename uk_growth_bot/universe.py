"""Investable universe: LSE-listed lines only (GBP account, no FX fees).

Verify each line before going live — tickers, accumulation class and UK
"reporting fund" status. A non-reporting offshore fund's gains are taxed as
income, not CGT, which would defeat the whole tax strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Asset:
    ticker: str          # yfinance ticker (".L" = London)
    name: str
    kind: str            # core_etf | stock | defensive
    base_weight: float = 0.0   # share of the CORE sleeve (core ETFs only)
    # A different fund tracking a similar index. Switching into it is a
    # disposal of the original for CGT, but not a re-purchase of the same
    # security, so HMRC's 30-day rule doesn't undo a harvested gain.
    twin: Optional[str] = None
    stamp_duty: bool = False   # UK shares / investment trusts pay 0.5% SDRT on buys

    @property
    def ibkr_symbol(self) -> str:
        return self.ticker.split(".")[0]


CORE: List[Asset] = [
    Asset("VWRP.L", "Vanguard FTSE All-World (Acc)", "core_etf", 0.50, twin="FWRG.L"),
    Asset("VUAG.L", "Vanguard S&P 500 (Acc)", "core_etf", 0.25, twin="CSP1.L"),
    Asset("CNX1.L", "iShares NASDAQ 100 (Acc)", "core_etf", 0.25, twin="EQQQ.L"),
]

TWINS: List[Asset] = [
    Asset("FWRG.L", "Invesco FTSE All-World (Acc)", "core_etf", twin="VWRP.L"),
    Asset("CSP1.L", "iShares Core S&P 500 (Acc)", "core_etf", twin="VUAG.L"),
    Asset("EQQQ.L", "Invesco EQQQ NASDAQ-100", "core_etf", twin="CNX1.L"),
]

DEFENSIVE = Asset("IGLT.L", "iShares Core UK Gilts", "defensive")

# Candidate pool the research step ranks for the satellite sleeve: UK-listed
# quality/growth compounders. Edit freely.
SATELLITE_CANDIDATES: List[Asset] = [
    Asset(t, n, "stock", stamp_duty=True) for t, n in [
        ("AZN.L", "AstraZeneca"), ("LSEG.L", "London Stock Exchange Group"),
        ("REL.L", "RELX"), ("RR.L", "Rolls-Royce"), ("III.L", "3i Group"),
        ("EXPN.L", "Experian"), ("HLMA.L", "Halma"), ("SGE.L", "Sage Group"),
        ("AUTO.L", "Auto Trader"), ("DPLM.L", "Diploma"), ("BA.L", "BAE Systems"),
        ("GAW.L", "Games Workshop"), ("CPG.L", "Compass Group"), ("SMT.L", "Scottish Mortgage IT"),
    ]
]

REGIME_INDEX = "VWRP.L"

ALL: Dict[str, Asset] = {a.ticker: a for a in CORE + TWINS + SATELLITE_CANDIDATES + [DEFENSIVE]}


def get(ticker: str) -> Asset:
    return ALL[ticker]


def core_slot(ticker: str) -> Optional[str]:
    """The CORE ticker whose slot *ticker* fills (itself or its twin)."""
    for a in CORE:
        if ticker in (a.ticker, a.twin):
            return a.ticker
    return None
