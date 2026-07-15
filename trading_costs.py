"""
trading_costs.py
================
Round-trip transaction-cost model, market-aware.

At small account sizes the fixed and percentage frictions dominate the
strategy's raw edge, so every entry decision, exit floor, and backtest
verdict must be computed NET of these costs.

India (Zerodha, NSE/BSE equity):
- Same-day round trip (buy + sell before close — charged as intraday even
  in the CNC product): STT 0.025% on the sell leg, stamp duty 0.003% on
  the buy leg, exchange txn ~0.00297% both legs, brokerage min(₹20, 0.03%)
  per executed order, 18% GST on (brokerage + txn), SEBI ₹10/crore.
- Overnight (delivery) round trip: STT 0.1% on BOTH legs, stamp 0.015% on
  buy, exchange txn both legs, **DP charge ₹15.93 flat per scrip on the
  demat sell** (the killer for small positions), GST, SEBI. Zero brokerage.

US (Alpaca): commission-free; SEC fee (sell) + TAF are a few hundredths of
a percent — modelled as a small flat percentage.

Both include an assumed slippage allowance per leg (spread + fill drift),
because the executor submits marketable limit orders.
"""

from __future__ import annotations

import os

ACTIVE_MARKET = os.getenv("TRADING_MARKET", "IN").upper()

# --- India (Zerodha) fee schedule -------------------------------------------
IN_STT_INTRADAY_SELL = 0.00025      # 0.025% sell leg
IN_STT_DELIVERY_EACH = 0.001        # 0.1% each leg
IN_STAMP_INTRADAY_BUY = 0.00003     # 0.003% buy leg
IN_STAMP_DELIVERY_BUY = 0.00015     # 0.015% buy leg
IN_EXCH_TXN_EACH = 0.0000297        # NSE ~0.00297% each leg
IN_BROKERAGE_PCT = 0.0003           # intraday: min(₹20, 0.03%) per order
IN_BROKERAGE_CAP = 20.0
IN_DP_CHARGE = 15.93                # CDSL/Zerodha DP fee per scrip per sell day (incl. GST)
IN_SEBI_EACH = 0.000001             # ₹10 per crore each leg
GST_RATE = 0.18

# --- US (Alpaca) -------------------------------------------------------------
US_ROUND_TRIP_PCT = 0.0002          # SEC + TAF, generously rounded (0.02%)

# --- Slippage allowance -------------------------------------------------------
# Marketable-limit fills drift from LTP by roughly the spread; per leg.
ASSUMED_SLIPPAGE_PER_LEG = 0.001    # 0.1% per leg -> 0.2% round trip


def round_trip_cost_pct(notional: float, overnight: bool = False,
                        market: str = None, include_slippage: bool = True) -> float:
    """
    Estimated round-trip cost as a FRACTION of notional (0.01 == 1%).

    Parameters
    ----------
    notional:
        Position value in local currency (qty × price). Fixed fees (the DP
        charge) are amortised over this, so small positions cost more in
        percentage terms.
    overnight:
        India only: True means the position is held into delivery (STT on
        both legs + DP charge on the demat sell).
    """
    market = (market or ACTIVE_MARKET).upper()
    if notional <= 0:
        return 1.0  # nonsense input — return 100% so any gate blocks it

    if market == "US":
        fees = US_ROUND_TRIP_PCT * notional
    elif overnight:
        stt = IN_STT_DELIVERY_EACH * notional * 2
        stamp = IN_STAMP_DELIVERY_BUY * notional
        txn = IN_EXCH_TXN_EACH * notional * 2
        gst = GST_RATE * txn  # delivery brokerage is zero at Zerodha
        sebi = IN_SEBI_EACH * notional * 2
        fees = stt + stamp + txn + gst + sebi + IN_DP_CHARGE
    else:
        stt = IN_STT_INTRADAY_SELL * notional
        stamp = IN_STAMP_INTRADAY_BUY * notional
        txn = IN_EXCH_TXN_EACH * notional * 2
        brokerage = 2 * min(IN_BROKERAGE_CAP, IN_BROKERAGE_PCT * notional)
        gst = GST_RATE * (brokerage + txn)
        sebi = IN_SEBI_EACH * notional * 2
        fees = stt + stamp + txn + brokerage + gst + sebi

    pct = fees / notional
    if include_slippage:
        pct += 2 * ASSUMED_SLIPPAGE_PER_LEG
    return pct


def min_required_move_pct(notional: float, edge_multiple: float = 2.0,
                          overnight: bool = False, market: str = None) -> float:
    """The smallest expected move worth trading: edge_multiple × round-trip cost."""
    return edge_multiple * round_trip_cost_pct(notional, overnight=overnight, market=market)
