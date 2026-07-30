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
  the buy leg, exchange txn ~0.00297% both legs, brokerage (see below),
  18% GST on (brokerage + txn), SEBI ₹10/crore. **No DP charge** — a
  same-day buy+sell nets to zero delivery, so nothing leaves the demat.
- Overnight (delivery) round trip: STT 0.1% on BOTH legs, stamp 0.015% on
  buy, exchange txn both legs, **DP charge ₹15.93 flat per scrip on the
  demat sell** (the killer for small positions), GST, SEBI.

BROKERAGE — calibrated against contract note CNT-26/27-67191195 (28 Jul
2026), the first real fee data we have. All four legs were billed at
EXACTLY 0.5000% (19.4610/3892.20, 20.3216/4064.32, 16.4682/3293.64,
15.6330/3126.60). This is Zerodha's NRI schedule, NOT the resident plan
(₹0 delivery / min(₹20, 0.03%) intraday) this model previously assumed.
The error was 11.5x: modelled 0.106% round trip vs 1.22% actually billed,
which is the entire ~₹80/day gap between our books and settled NAV.
That note also DISPROVED the old delivery assumptions for this account:
no DP charge appeared, and STT came in at the intraday sell-only rate.

The NRI schedule caps brokerage per executed order (believed ₹100), but
0.5% bound on every observed leg so the cap is UNVERIFIED — it defaults
to infinity here. That over-states cost for orders above the cap, which
is the safe direction for a gate. Set IN_BROKERAGE_CAP once confirmed:
the cap is the only thing that makes size a lever, taking a round trip
from 1.00% at ₹20k to 0.20% at ₹1L.

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
# Per executed order, EITHER product. Measured at 0.5000% on all four legs
# of contract note CNT-26/27-67191195; override if the account plan changes.
IN_BROKERAGE_PCT = float(os.getenv("IN_BROKERAGE_PCT", "0.005"))
# Per-order cap. UNVERIFIED (0.5% bound on every leg we have seen); believed
# ₹100 on the NRI plan. Defaults to no cap, which over-states cost rather
# than under-states it. Set IN_BROKERAGE_CAP=100 once a contract note with
# an order above ₹20,000 confirms it.
IN_BROKERAGE_CAP = float(os.getenv("IN_BROKERAGE_CAP", "inf"))
# CDSL/Zerodha DP fee per scrip per delivery-sell day (incl. GST). Measured
# from the fund ledger: four debits on 2026-07-17 (ITC, HINDUNILVR, BFINVEST,
# ICICIBANK), each exactly ₹15.34 — not the ₹15.93 previously assumed. Those
# four are the ONLY DP debits in six weeks, confirming same-day round trips
# never incur it.
IN_DP_CHARGE = 15.34
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
        # Delivery brokerage is NOT zero on this account — the NRI schedule
        # bills 0.5%/leg on both products, so the resident plan's free
        # delivery never applied here.
        brokerage = 2 * min(IN_BROKERAGE_CAP, IN_BROKERAGE_PCT * notional)
        gst = GST_RATE * (brokerage + txn)
        sebi = IN_SEBI_EACH * notional * 2
        fees = stt + stamp + txn + brokerage + gst + sebi + IN_DP_CHARGE
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


def net_breakeven_pct(notional: float, overnight: bool = False,
                      market: str = None) -> float:
    """
    Gain from entry at which an EXIT nets zero — the floor a profit-lock stop
    must never sit below.

    Deliberately NOT ``round_trip_cost_pct``, which adds slippage for BOTH
    legs. For an exit decision that double-counts:

    * fees (both legs) are real cash deductions and must be recovered;
    * ENTRY slippage is already inside ``entry_price`` — it is the fill we
      actually got, so charging it again would demand profit twice;
    * EXIT slippage has not happened yet. The stop is a TRIGGER and the fill
      lands roughly a spread below it, so exactly one leg is still owed.

    Hence fees + one leg. Same reasoning as ``TradingDB._net_pnl``, which
    excludes slippage entirely because it works from realised fills on both
    sides.
    """
    fees = round_trip_cost_pct(notional, overnight=overnight, market=market,
                               include_slippage=False)
    return fees + ASSUMED_SLIPPAGE_PER_LEG


#: A profit-lock must arm strictly ABOVE net break-even, never at it. Arming AT
#: break-even puts the trigger on the current price and fires instantly for zero
#: net gain; arming BELOW it guarantees a net loss on the very next tick. This
#: multiple buys enough headroom that there is real profit to protect.
PROFIT_LOCK_ARM_MULTIPLE = 1.25


def profit_lock_arm_pct(notional: float, configured_threshold: float,
                        overnight: bool = False, market: str = None) -> float:
    """
    Gain from entry at which the profit-lock trail may arm.

    ``configured_threshold`` (config.risk.profit_lock_threshold) acts as a
    FLOOR, so a market that wants to wait longer than costs require still
    does. Cost sets a hard minimum:

        arm = max(configured, net_breakeven × PROFIT_LOCK_ARM_MULTIPLE)

    Why this is not a constant: the hardcoded thresholds (US +0.75%, IN +1.0%)
    were chosen when IN friction was believed to be ~0.1%. At the real 1.215%
    the IN threshold sat BELOW break-even, so the moment a position touched
    +1.0% the lock armed, the break-even floor (already above the price) became
    the trigger, and it sold instantly at +1.00% gross = −0.41% NET. Every IN
    winner was exited at a guaranteed loss the instant it became a winner.

    US is unaffected (0.12% break-even × 1.25 = 0.15% < the configured 0.75%).
    """
    return max(configured_threshold,
               net_breakeven_pct(notional, overnight=overnight, market=market)
               * PROFIT_LOCK_ARM_MULTIPLE)


def min_required_move_pct(notional: float, edge_multiple: float = 2.0,
                          overnight: bool = False, market: str = None) -> float:
    """The smallest expected move worth trading: edge_multiple × round-trip cost."""
    return edge_multiple * round_trip_cost_pct(notional, overnight=overnight, market=market)
