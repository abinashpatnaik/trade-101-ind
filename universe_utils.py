"""
universe_utils
==============
Small, dependency-free helpers for shaping the tradeable universe.

Kept separate from ``market_screener`` (which pulls yfinance at import time) so
the trader can dedup its target list without dragging in that weight.
"""

from __future__ import annotations

from typing import Iterable, List


def prefer_nse(symbols: Iterable[str]) -> List[str]:
    """Drop the BSE (``.BO``) listing of any stock also present as NSE (``.NS``).

    A stock dual-listed on both exchanges must be traded once, on the more
    liquid NSE leg — never on both (which splits capital and pays friction
    twice). BSE-only names (no ``.NS`` twin in the list) are kept, as are all
    suffixless / non-Indian symbols. Input order is preserved and duplicates
    are collapsed.

    >>> prefer_nse(["ICICIBANK.NS", "ICICIBANK.BO", "BFINVEST.BO", "AAPL"])
    ['ICICIBANK.NS', 'BFINVEST.BO', 'AAPL']
    """
    syms = list(symbols)
    nse_bases = {s[:-3] for s in syms if s.endswith(".NS")}
    out: List[str] = []
    seen = set()
    for s in syms:
        if s.endswith(".BO") and s[:-3] in nse_bases:
            continue  # dual-listed → keep only the .NS leg
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out
