"""prefer_nse: a stock dual-listed on NSE+BSE trades once, on the NSE leg."""

from universe_utils import prefer_nse


def test_dual_listed_collapses_to_nse():
    assert prefer_nse(["ICICIBANK.NS", "ICICIBANK.BO"]) == ["ICICIBANK.NS"]


def test_drops_bo_even_when_it_comes_first():
    # The exact mid-July case: both legs of the same name were traded.
    assert prefer_nse(["ITC.BO", "ITC.NS"]) == ["ITC.NS"]


def test_bse_only_name_is_kept():
    # No .NS twin in the list → BSE-only, keep it.
    assert prefer_nse(["BFINVEST.BO"]) == ["BFINVEST.BO"]


def test_mixed_universe_order_preserved():
    got = prefer_nse(
        ["HINDUNILVR.NS", "HINDUNILVR.BO", "BFINVEST.BO", "RELIANCE.NS", "ITC.BO", "ITC.NS"]
    )
    assert got == ["HINDUNILVR.NS", "BFINVEST.BO", "RELIANCE.NS", "ITC.NS"]


def test_us_and_suffixless_untouched():
    assert prefer_nse(["AAPL", "MSFT"]) == ["AAPL", "MSFT"]


def test_duplicates_collapsed():
    assert prefer_nse(["TCS.NS", "TCS.NS"]) == ["TCS.NS"]
