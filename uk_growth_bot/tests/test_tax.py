from datetime import date

import pytest

from uk_growth_bot.tax import (
    Txn, disposals_for_symbol, estimate_sale_gain, in_harvest_window,
    pool_cost_basis, recently_sold, tax_position, tax_year_of,
)

D = date


def buy(d, q, p, fees=0.0, sym="VWRP.L"):
    return Txn(d, sym, "BUY", q, p, fees)


def sell(d, q, p, fees=0.0, sym="VWRP.L"):
    return Txn(d, sym, "SELL", q, p, fees)


def test_tax_year_boundary():
    assert tax_year_of(D(2026, 4, 5)) == 2025
    assert tax_year_of(D(2026, 4, 6)) == 2026


def test_section_104_pool_uses_average_cost_and_fees():
    txns = [buy(D(2025, 5, 1), 100, 1.0, fees=3), buy(D(2025, 6, 1), 100, 2.0, fees=3),
            sell(D(2025, 9, 1), 100, 3.0, fees=3)]
    (d,) = disposals_for_symbol(txns)
    assert d.proceeds == pytest.approx(297)
    assert d.cost == pytest.approx(153)       # half of (100+3 + 200+3)
    assert d.gain == pytest.approx(144)


def test_same_day_rule_beats_pool():
    txns = [buy(D(2025, 5, 1), 100, 1.0), buy(D(2025, 7, 1), 50, 2.0), sell(D(2025, 7, 1), 50, 3.0)]
    (d,) = disposals_for_symbol(txns)
    assert d.cost == pytest.approx(100)
    assert d.matches[0].startswith("same-day")


def test_bed_and_breakfast_rule_cancels_a_harvested_loss():
    txns = [buy(D(2025, 5, 1), 100, 1.0), sell(D(2025, 8, 1), 100, 0.5), buy(D(2025, 8, 11), 100, 0.6)]
    (d,) = disposals_for_symbol(txns)
    assert d.cost == pytest.approx(60)        # matched to the re-buy, not the £100 pool
    assert d.gain == pytest.approx(-10)
    held, cost = pool_cost_basis(txns, "VWRP.L")
    assert held == 100 and cost == pytest.approx(100)


def test_rebuy_after_31_days_uses_pool():
    txns = [buy(D(2025, 5, 1), 100, 1.0), sell(D(2025, 8, 1), 100, 0.5), buy(D(2025, 9, 1), 100, 0.6)]
    (d,) = disposals_for_symbol(txns)
    assert d.gain == pytest.approx(-50)


def test_tax_position_nets_losses_and_applies_allowance():
    txns = [buy(D(2026, 5, 1), 100, 10.0), sell(D(2026, 6, 1), 100, 60.0, sym="VWRP.L"),
            buy(D(2026, 5, 1), 100, 10.0, sym="AZN.L"), sell(D(2026, 7, 1), 100, 5.0, sym="AZN.L")]
    pos = tax_position(txns, [(D(2026, 8, 1), 600.0)], on=D(2026, 10, 1))
    assert pos.net_gains == pytest.approx(4500)
    assert pos.allowance_remaining == 0
    assert pos.estimated_cgt == pytest.approx(1500 * pos.cgt_rate)
    assert pos.estimated_dividend_tax == pytest.approx(100 * pos.div_rate)
    assert pos.must_report


def test_estimate_and_wash_guard():
    txns = [buy(D(2026, 5, 1), 10, 100.0, fees=3)]
    assert estimate_sale_gain(txns, "VWRP.L", 5, 120.0, 3, D(2026, 9, 1)) == pytest.approx(600 - 3 - 501.5)
    txns.append(sell(D(2026, 9, 1), 5, 120.0))
    assert recently_sold(txns, "VWRP.L", D(2026, 9, 20)) == D(2026, 9, 1)
    assert recently_sold(txns, "VWRP.L", D(2026, 10, 5)) is None


def test_harvest_window():
    assert in_harvest_window(D(2027, 3, 15))
    assert in_harvest_window(D(2027, 4, 5))
    assert not in_harvest_window(D(2027, 4, 6))
