"""
Ledger reconciliation: the rule that makes dashboard numbers match the bank.

Ground truth is the Zerodha fund ledger + P&L book for 15 Jun - 29 Jul 2026
(client YIX026), cross-checked against contract note CNT-26/27-67191195:

    deposits            20,000.00     two UPI credits
    closing balance     17,357.5374
    ---------------------------------
    actual result       -2,642.4626

    gross trade P&L       -457.36     sell value - buy value, 27 symbols
    trade charges       -1,505.7423   brokerage 1,228.3876 + GST 222.52 + rest
    DP charges              -61.36    4 x 15.34, delivery sells on 17 Jul
    account overheads      -618.00    Kite Connect API 500.0002 + DDPI 118.00
    ---------------------------------
    explained           -2,642.4623

The identity is what these tests defend. It closes to 0.0003 rupees, and the
only reason it was ever open is that overheads and funding were invisible to
a system that only sees trade settlements.
"""

import pytest

from db import TradingDB
from scripts.import_zerodha_ledger import classify

# Real figures from the P&L book (see module docstring).
GROSS_TRADE_PNL = -457.36
TRADE_CHARGES = -1505.7423
DP_CHARGES = -61.36
CLOSING_BALANCE = 17357.5374
DEPOSITS = 20000.0


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_DB_PATH", str(tmp_path / "t.db"))
    import db as db_mod
    monkeypatch.setattr(db_mod, "_DB_PATH", str(tmp_path / "t.db"),
                        raising=False)
    return TradingDB(str(tmp_path / "t.db"))


# ------------------------------------------------------- classification rule

@pytest.mark.parametrize("text,expected", [
    ("Funds added using UPI from YIX026 with reference number 945700301876",
     "funding"),
    ("Kite Connect API Charges (2026-07-07)", "overhead"),
    ("Charges for enabling DDPI", "overhead"),
    ("Provisional TDS amount blocked for 2026133", "transient"),
    ("Provisional TDS amount reversed for 2026133", "transient"),
])
def test_ledger_lines_classify(text, expected):
    assert classify(text) == expected


@pytest.mark.parametrize("text", [
    "Net settlement for Equity with settlement number: 2026140",
    "DP Charges for Sale of ITC on 17/07/2026",
    "Opening Balance",
    "Closing Balance",
])
def test_trade_attributable_lines_are_never_imported(text):
    """Settlements are already the bot's trade P&L and DP charges belong to a
    specific delivery sell (trading_costs.IN_DP_CHARGE). Importing either as
    an account charge double-counts it against the same rupees."""
    assert classify(text) is None


def test_unknown_debits_default_to_overhead():
    """Unrecognised lines must land somewhere that COSTS the strategy.
    Silently dropping a real debit flatters performance, which is the more
    dangerous direction to be wrong."""
    assert classify("Some New Zerodha Fee 2027") == "overhead"


# ------------------------------------------------------------- the identity

def test_reconciliation_identity_closes(db):
    """NAV - funding == trade P&L + charges + DP + overheads."""
    db.record_account_charge("2026-07-06", "Funds added using UPI", 10000.0, "funding")
    db.record_account_charge("2026-07-27", "Funds added using UPI 2", 10000.0, "funding")
    db.record_account_charge("2026-07-07", "Kite Connect API Charges", -500.0002, "overhead")
    db.record_account_charge("2026-07-16", "Charges for enabling DDPI", -118.0, "overhead")
    db.record_account_charge("2026-07-17", "Provisional TDS blocked", -1645.7678, "transient")
    db.record_account_charge("2026-07-20", "Provisional TDS reversed", 1645.7678, "transient")

    c = db.get_account_charges()
    assert c["funding"] == pytest.approx(DEPOSITS)
    assert c["overhead"] == pytest.approx(-618.0)

    actual = CLOSING_BALANCE - c["funding"]
    explained = GROSS_TRADE_PNL + TRADE_CHARGES + DP_CHARGES + c["overhead"]
    assert actual == pytest.approx(explained, abs=0.01)
    assert actual == pytest.approx(-2642.46, abs=0.01)


def test_transient_entries_net_to_zero(db):
    """The provisional-TDS trap: 1,645.77 was blocked on 17 Jul and reversed
    on 20 Jul. Counting either leg alone invents a loss or a gain of 6% of
    NAV that never happened."""
    db.record_account_charge("2026-07-17", "Provisional TDS blocked", -1645.7678, "transient")
    db.record_account_charge("2026-07-20", "Provisional TDS reversed", 1645.7678, "transient")
    assert db.get_account_charges()["transient"] == pytest.approx(0.0)


def test_funding_is_excluded_from_performance(db):
    """A deposit raises NAV without earning anything. If funding leaked into
    P&L, the 27 Jul top-up would have shown as a +10,000 profit."""
    db.record_account_charge("2026-07-27", "Funds added using UPI", 10000.0, "funding")
    c = db.get_account_charges()
    assert c["overhead"] == 0.0          # funding must not land in overheads
    assert c["funding"] == pytest.approx(10000.0)


def test_overheads_are_not_per_trade(db):
    """Overheads must stay out of pnl_net. The Kite API fee is charged monthly
    whether the bot trades 0 or 200 times; amortising it into trades would
    corrupt win rate and expectancy for trades that never incurred it."""
    db.record_account_charge("2026-07-07", "Kite Connect API Charges", -500.0002, "overhead")
    db.insert_trade("2026-07-07", "09:20:00", "DIACABS", "SELL", 13, 312.64,
                    4064.32, pnl=172.12, mode="live")
    realized = db.get_realized_pnl(mode="live", net=True)["realizedPnl"]
    # pnl_net carries trade friction only — nowhere near the 500 overhead.
    assert realized > 100.0
    assert db.get_account_charges()["overhead"] == pytest.approx(-500.0002)


def test_record_account_charge_is_idempotent(db):
    """Re-importing an overlapping ledger export must not double-count."""
    for _ in range(3):
        db.record_account_charge("2026-07-07", "Kite Connect API Charges",
                                 -500.0002, "overhead")
    assert db.get_account_charges()["overhead"] == pytest.approx(-500.0002)
    assert db.get_account_charges()["count"] == 1


def test_unknown_kind_is_rejected(db):
    with pytest.raises(ValueError):
        db.record_account_charge("2026-07-07", "x", -1.0, "not_a_kind")
