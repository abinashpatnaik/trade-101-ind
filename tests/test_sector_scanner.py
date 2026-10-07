"""
sector_scanner.py's "Sector Rotation" scanner had no real sector data to
work with: get_us_dynamic_universe() scraped "Russell_1000_Index" on
Wikipedia, which doesn't carry a constituent table at all (confirmed
2026-10-07 in the live us-scanner container: "Could not find Ticker/Symbol
column in Wikipedia tables" every single run) -- so it silently fell back
to the static config.universe.tickers list every time, meaning "dynamic
universe scanning" never discovered anything beyond that same ~30 names.
On top of that, every stock was tagged with one placeholder sector
("Dynamic US"), so the "top 2 rising sectors" grouping always degenerated
to the entire universe -- no real sector filtering ever happened either.

Switched to "List_of_S%26P_500_companies" (actively maintained, has both
a ticker column and a real GICS Sector column) and threaded that sector
through to select_rising_sector_candidates(), which is what actually
makes the sector-rotation filtering real.
"""

import market_screener
from sector_scanner import select_rising_sector_candidates


def _metrics(**by_symbol):
    """by_symbol: symbol -> (momentum, sentiment, sector)."""
    return {
        sym: {"momentum": mom, "sentiment": sent, "sector": sector}
        for sym, (mom, sent, sector) in by_symbol.items()
    }


def test_the_original_bug_one_fake_sector_never_filters_anything():
    """Regression check for the exact prior behaviour: every stock sharing
    one sector means "top sectors" is just that one sector, and every
    positive-momentum stock becomes a candidate regardless of how it
    compares to anything else -- no real selectivity."""
    stock_metrics = _metrics(
        AAA=(0.10, 0.5, "Dynamic US"),
        BBB=(0.01, 0.0, "Dynamic US"),
        CCC=(-0.05, -0.2, "Dynamic US"),
    )
    top_sectors, candidates = select_rising_sector_candidates(stock_metrics)
    assert top_sectors == ["Dynamic US"]
    # AAA and BBB both pass (positive momentum, non-negative sentiment) --
    # nothing about "rising sector" membership excluded anything, because
    # there was only ever one sector to belong to.
    assert set(candidates) == {"AAA", "BBB"}


def test_real_sectors_actually_exclude_weak_ones():
    """With genuine sector variety (3+ sectors, so "top 2" is a real cut),
    a stock with positive momentum in the WEAKEST sector must NOT become a
    candidate -- this is what was impossible to express before real
    sector data existed (every stock shared one placeholder sector)."""
    stock_metrics = _metrics(
        # Tech: strongest average momentum -> a top sector.
        AAPL=(0.08, 0.3, "Information Technology"),
        MSFT=(0.06, 0.2, "Information Technology"),
        # Health Care: second strongest -> the other top sector.
        LLY=(0.04, 0.1, "Health Care"),
        UNH=(0.03, 0.1, "Health Care"),
        # Utilities: weakest average momentum -> must be cut from top 2.
        NEE=(0.01, 0.0, "Utilities"),
        DUK=(-0.02, -0.1, "Utilities"),
        # A single positive-momentum utility stock -- must still be
        # excluded because its SECTOR isn't rising, even though it
        # individually clears the momentum/sentiment bar.
        SO=(0.03, 0.1, "Utilities"),
    )
    top_sectors, candidates = select_rising_sector_candidates(stock_metrics)
    assert top_sectors == ["Information Technology", "Health Care"]
    assert "SO" not in candidates, "a lone decent stock in the weakest sector must be excluded"
    assert set(candidates) == {"AAPL", "MSFT", "LLY", "UNH"}


def test_sector_needs_at_least_two_members_to_be_ranked():
    stock_metrics = _metrics(
        LONE=(0.50, 1.0, "Solo Sector"),
        AAPL=(0.02, 0.1, "Information Technology"),
        MSFT=(0.02, 0.1, "Information Technology"),
    )
    top_sectors, _ = select_rising_sector_candidates(stock_metrics)
    assert "Solo Sector" not in top_sectors


def test_negative_momentum_or_negative_sentiment_excluded_even_in_top_sector():
    stock_metrics = _metrics(
        AAPL=(0.08, 0.3, "Information Technology"),
        MSFT=(-0.01, 0.2, "Information Technology"),  # negative momentum
        NVDA=(0.05, -0.1, "Information Technology"),  # negative sentiment
    )
    top_sectors, candidates = select_rising_sector_candidates(stock_metrics)
    assert candidates == ["AAPL"]


# --- get_us_dynamic_universe: fallback shape consistency -------------------


def test_fallback_on_scrape_failure_returns_ticker_sector_pairs(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(market_screener.requests, "get", _raise)
    result = market_screener.get_us_dynamic_universe(50)

    assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in result)
    from config import config
    assert [t for t, _ in result] == config.universe.tickers
    assert all(sector == "Unknown" for _, sector in result)
