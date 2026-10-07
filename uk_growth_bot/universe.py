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
    # Core funds: the bot holds at most one fund per group, so it can't load
    # up on a single theme (e.g. Nasdaq-100 AND semiconductors).
    group: str = ""
    # A different fund tracking a similar index. Switching into it is a
    # disposal of the original for CGT, but not a re-purchase of the same
    # security, so HMRC's 30-day rule doesn't undo a harvested gain (GIA only).
    twin: Optional[str] = None
    stamp_duty: bool = False   # UK shares / investment trusts pay 0.5% SDRT on buys

    @property
    def ibkr_symbol(self) -> str:
        return self.ticker.split(".")[0]


# The core sleeve holds the best-ranked few of these (settings.core_funds),
# at most one per group. Lines must be priced in GBP and tradable on the
# broker; anything that isn't is dropped at runtime before planning.
CORE_POOL: List[Asset] = [
    Asset("VWRP.L", "Vanguard FTSE All-World (Acc)", "core_etf", "global", twin="FWRG.L"),
    Asset("VUAG.L", "Vanguard S&P 500 (Acc)", "core_etf", "us_large", twin="CSP1.L"),
    Asset("CNX1.L", "iShares NASDAQ 100 (Acc)", "core_etf", "tech", twin="EQQQ.L"),
    Asset("SMGB.L", "VanEck Semiconductor", "core_etf", "tech"),
    Asset("EMIM.L", "iShares Core MSCI EM IMI (Acc)", "core_etf", "emerging"),
    Asset("WLDS.L", "iShares MSCI World Small Cap (Acc)", "core_etf", "small_cap"),
    Asset("IWQU.L", "iShares MSCI World Quality Factor", "core_etf", "quality"),
    Asset("VMID.L", "Vanguard FTSE 250", "core_etf", "uk_mid"),
]

TWINS: List[Asset] = [
    Asset("FWRG.L", "Invesco FTSE All-World (Acc)", "core_etf", "global", twin="VWRP.L"),
    Asset("CSP1.L", "iShares Core S&P 500 (Acc)", "core_etf", "us_large", twin="VUAG.L"),
    Asset("EQQQ.L", "Invesco EQQQ NASDAQ-100", "core_etf", "tech", twin="CNX1.L"),
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
        ("GAW.L", "Games Workshop"), ("SMT.L", "Scottish Mortgage IT"),
    ]
]

REGIME_INDEX = "VWRP.L"

ALL: Dict[str, Asset] = {a.ticker: a for a in CORE_POOL + TWINS + SATELLITE_CANDIDATES + [DEFENSIVE]}


def get(ticker: str) -> Asset:
    return ALL[ticker]


def core_slot(ticker: str) -> Optional[str]:
    """The core-pool fund whose slot *ticker* fills (itself or its twin)."""
    for a in CORE_POOL:
        if ticker in (a.ticker, a.twin):
            return a.ticker
    return None
