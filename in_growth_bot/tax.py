"""Indian capital-gains tax on listed shares (and equity ETFs).

Lots are matched first-in-first-out, as for demat holdings. A lot held for
more than 12 months gives a long-term gain (LTCG, 12.5% above the yearly
Rs1.25 lakh exemption); otherwise a short-term gain (STCG, 20%). Short-term
losses can be set off against either kind of gain, long-term losses only
against long-term gains. Brokerage, STT-excluded charges and stamp duty are
part of cost; sale charges reduce proceeds. STT itself is not deductible but
is small; it's treated like the other charges here.

An estimate to steer decisions and fill the weekly report. Zerodha's tax P&L
and your ITR are the authority. As an NRI, tax on gains is deducted at
source (TDS) when you sell.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Tuple

from .config import IN_TAX_YEARS


@dataclass
class Txn:
    day: date
    symbol: str
    side: str          # BUY | SELL
    quantity: float
    price: float       # INR per share
    fees: float = 0.0  # all charges, INR
    fx: float = 0.0    # INR per GBP on the day (for the UK view); 0 = unknown


@dataclass
class Disposal:
    day: date
    symbol: str
    quantity: float
    proceeds: float
    cost: float
    st_gain: float = 0.0
    lt_gain: float = 0.0

    @property
    def gain(self) -> float:
        return self.proceeds - self.cost


def fy_of(d: date) -> int:
    """Start year of the Indian financial year containing *d* (1 Apr - 31 Mar)."""
    return d.year if d.month >= 4 else d.year - 1


def fy_bounds(year: int) -> Tuple[date, date]:
    return date(year, 4, 1), date(year + 1, 3, 31)


def rates_for(year: int) -> Dict[str, float]:
    known = [k for k in IN_TAX_YEARS if k <= year]
    return IN_TAX_YEARS[max(known) if known else min(IN_TAX_YEARS)]


def one_year_after(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year + 1, day=28)


def is_long_term(bought: date, sold: date) -> bool:
    return sold > one_year_after(bought)


@dataclass
class _Lot:
    day: date
    qty: float
    unit: float  # INR cost per share incl. buy charges


def _replay(txns: List[Txn]) -> Tuple[List[Disposal], Dict[str, deque]]:
    lots: Dict[str, deque] = defaultdict(deque)
    out: List[Disposal] = []
    for t in sorted(txns, key=lambda t: (t.day, t.side != "BUY")):
        if t.side == "BUY":
            if t.quantity > 0:
                lots[t.symbol].append(_Lot(t.day, t.quantity, (t.quantity * t.price + t.fees) / t.quantity))
            continue
        proceeds = t.quantity * t.price - t.fees
        d = Disposal(t.day, t.symbol, t.quantity, proceeds, 0.0)
        left, q = lots[t.symbol], t.quantity
        while q > 1e-9 and left:
            lot = left[0]
            take = min(q, lot.qty)
            cost = take * lot.unit
            part_proceeds = proceeds * take / t.quantity
            if is_long_term(lot.day, t.day):
                d.lt_gain += part_proceeds - cost
            else:
                d.st_gain += part_proceeds - cost
            d.cost += cost
            lot.qty -= take
            q -= take
            if lot.qty <= 1e-9:
                left.popleft()
        out.append(d)
    return out, lots


def all_disposals(txns: List[Txn]) -> List[Disposal]:
    return _replay(txns)[0]


def open_lots(txns: List[Txn], symbol: str) -> List[_Lot]:
    return list(_replay([t for t in txns if t.symbol == symbol])[1][symbol])


def cost_basis(txns: List[Txn], symbol: str) -> Tuple[float, float]:
    """(quantity, total cost) still held, FIFO."""
    lots = open_lots(txns, symbol)
    return sum(l.qty for l in lots), sum(l.qty * l.unit for l in lots)


def long_term_qty(txns: List[Txn], symbol: str, on: date) -> float:
    return sum(l.qty for l in open_lots(txns, symbol) if is_long_term(l.day, on))


def estimate_sale(txns: List[Txn], symbol: str, quantity: float, price: float,
                  fees: float, on: date) -> Disposal:
    hypo = [t for t in txns if t.symbol == symbol] + [Txn(on, symbol, "SELL", quantity, price, fees)]
    return all_disposals(hypo)[-1]


@dataclass
class TaxPosition:
    fy: int
    st_gains: float      # net short-term (may be negative)
    lt_gains: float      # net long-term (may be negative)
    proceeds: float
    exemption: float
    stcg_rate: float
    ltcg_rate: float
    cess: float
    disposals: List[Disposal] = field(default_factory=list)

    @property
    def lt_after_setoff(self) -> float:
        # Short-term losses may reduce long-term gains.
        return self.lt_gains + min(0.0, self.st_gains)

    @property
    def exemption_remaining(self) -> float:
        return max(0.0, self.exemption - max(0.0, self.lt_after_setoff))

    @property
    def estimated_tax(self) -> float:
        st = max(0.0, self.st_gains) * self.stcg_rate
        lt = max(0.0, self.lt_after_setoff - self.exemption) * self.ltcg_rate
        return (st + lt) * (1 + self.cess)

    @property
    def label(self) -> str:
        return f"FY {self.fy}-{str(self.fy + 1)[-2:]}"


def tax_position(txns: List[Txn], on: date) -> TaxPosition:
    year = fy_of(on)
    start, end = fy_bounds(year)
    disp = [d for d in all_disposals(txns) if start <= d.day <= end]
    r = rates_for(year)
    return TaxPosition(
        fy=year,
        st_gains=sum(d.st_gain for d in disp),
        lt_gains=sum(d.lt_gain for d in disp),
        proceeds=sum(d.proceeds for d in disp),
        exemption=r["ltcg_exemption"],
        stcg_rate=r["stcg"],
        ltcg_rate=r["ltcg"],
        cess=r["cess"],
        disposals=disp,
    )


def in_harvest_window(on: date) -> bool:
    """Feb-Mar: the financial year ends on 31 March."""
    return on.month in (2, 3)
