from datetime import date

import pytest

from in_growth_bot.tax import (Txn, cost_basis, estimate_sale, fy_of, is_long_term, long_term_qty,
                               tax_position)


def test_financial_year_runs_april_to_march():
    assert fy_of(date(2027, 3, 31)) == 2026 and fy_of(date(2027, 4, 1)) == 2027


def test_long_term_means_more_than_twelve_months():
    assert not is_long_term(date(2026, 1, 10), date(2027, 1, 10))
    assert is_long_term(date(2026, 1, 10), date(2027, 1, 11))
    assert is_long_term(date(2024, 2, 29), date(2025, 3, 1))


def test_fifo_splits_a_sale_into_long_and_short_term():
    txns = [Txn(date(2025, 1, 2), "TCS.NS", "BUY", 10, 100.0),
            Txn(date(2026, 6, 1), "TCS.NS", "BUY", 10, 150.0)]
    d = estimate_sale(txns, "TCS.NS", 15, 200.0, 0.0, date(2026, 9, 1))
    assert d.lt_gain == pytest.approx(10 * 100)     # oldest lot, held > 1 year
    assert d.st_gain == pytest.approx(5 * 50)       # half the newer lot
    assert long_term_qty(txns, "TCS.NS", date(2026, 9, 1)) == 10
    assert cost_basis(txns, "TCS.NS") == (20, pytest.approx(2500))


def test_exemption_and_tax_with_setoff():
    txns = [Txn(date(2024, 5, 2), "A.NS", "BUY", 100, 1000.0), Txn(date(2025, 6, 2), "A.NS", "SELL", 100, 3000.0),
            Txn(date(2025, 8, 1), "B.NS", "BUY", 10, 1000.0), Txn(date(2025, 9, 1), "B.NS", "SELL", 10, 500.0)]
    tp = tax_position(txns, date(2025, 12, 1))
    assert tp.lt_gains == pytest.approx(200000) and tp.st_gains == pytest.approx(-5000)
    assert tp.lt_after_setoff == pytest.approx(195000)
    assert tp.exemption_remaining == 0
    assert tp.estimated_tax == pytest.approx((195000 - 125000) * 0.125 * 1.04)


def test_sale_charges_reduce_proceeds_and_buy_charges_add_to_cost():
    txns = [Txn(date(2025, 1, 2), "A.NS", "BUY", 10, 100.0, fees=20.0)]
    d = estimate_sale(txns, "A.NS", 10, 120.0, 30.0, date(2026, 3, 1))
    assert d.cost == pytest.approx(1020) and d.proceeds == pytest.approx(1170)
