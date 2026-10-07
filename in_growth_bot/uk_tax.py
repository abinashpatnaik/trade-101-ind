"""UK Capital Gains Tax view of the Indian holdings, in GBP.

As a UK resident you are taxed in the UK on these gains too; Indian tax paid
is credited against the UK tax on the same gain (India-UK DTAA). Each trade
is converted at that day's GBP/INR rate.


Implements HMRC share-matching (TCGA 1992 s.104-106A):
  1. same-day acquisitions,
  2. acquisitions in the following 30 days ("bed and breakfast" rule),
  3. the Section 104 pool (average cost).
Commission and stamp duty are allowable costs; sale commission reduces proceeds.

This is an estimate for the weekly report. Your Self Assessment return is
the authority.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List

from .config import UK_TAX_YEARS as TAX_YEARS, settings

BED_AND_BREAKFAST_DAYS = 30
SA_PROCEEDS_REPORTING_LIMIT = 50_000.0


@dataclass
class Txn:
    day: date
    symbol: str
    side: str        # BUY | SELL
    quantity: float
    price: float     # GBP per share
    fees: float = 0.0  # commission + stamp duty


@dataclass
class Disposal:
    day: date
    symbol: str
    quantity: float
    proceeds: float
    cost: float
    matches: List[str] = field(default_factory=list)

    @property
    def gain(self) -> float:
        return self.proceeds - self.cost


def tax_year_of(d: date) -> int:
    """Start year of the UK tax year containing *d* (6 Apr - 5 Apr)."""
    return d.year if (d.month, d.day) >= (4, 6) else d.year - 1


def tax_year_bounds(year: int) -> tuple[date, date]:
    return date(year, 4, 6), date(year + 1, 4, 5)


def rates_for(year: int) -> Dict[str, float]:
    # Unknown future years fall back to the latest known figures.
    known = [k for k in TAX_YEARS if k <= year]
    return TAX_YEARS[max(known) if known else min(TAX_YEARS)]


def _cgt_rate(year: int) -> float:
    r = rates_for(year)
    return r["cgt_basic"] if settings.uk_income_tax_band == "basic" else r["cgt_higher"]


def _div_rate(year: int) -> float:
    return rates_for(year)[f"div_{settings.uk_income_tax_band}"]


@dataclass
class _Lot:
    day: date
    qty: float
    unit: float  # GBP per share (cost for buys, net proceeds for sells)
    left: float = -1.0

    def __post_init__(self) -> None:
        if self.left < 0:
            self.left = self.qty


def _day_lots(txns: List[Txn], side: str) -> List[_Lot]:
    # HMRC treats all same-day acquisitions (or disposals) of one security as one.
    agg: Dict[date, List[float]] = defaultdict(lambda: [0.0, 0.0])
    for t in txns:
        if t.side == side:
            value = t.quantity * t.price + (t.fees if side == "BUY" else -t.fees)
            agg[t.day][0] += t.quantity
            agg[t.day][1] += value
    return [_Lot(d, q, v / q) for d, (q, v) in sorted(agg.items()) if q > 0]


def disposals_for_symbol(txns: List[Txn]) -> List[Disposal]:
    buys = _day_lots(txns, "BUY")
    sells = _day_lots(txns, "SELL")
    symbol = txns[0].symbol if txns else ""
    out = {s.day: Disposal(s.day, symbol, s.qty, s.qty * s.unit, 0.0) for s in sells}

    # 1. Same day
    for s in sells:
        for b in buys:
            if b.day == s.day and b.left > 0 and s.left > 0:
                q = min(b.left, s.left)
                b.left -= q
                s.left -= q
                out[s.day].cost += q * b.unit
                out[s.day].matches.append(f"same-day {q:g}")

    # 2. Following 30 days, earliest disposal first, earliest acquisition first
    for s in sells:
        window_end = s.day + timedelta(days=BED_AND_BREAKFAST_DAYS)
        for b in buys:
            if s.day < b.day <= window_end and b.left > 0 and s.left > 0:
                q = min(b.left, s.left)
                b.left -= q
                s.left -= q
                out[s.day].cost += q * b.unit
                out[s.day].matches.append(f"30-day {b.day.isoformat()} {q:g}")

    # 3. Section 104 pool, chronologically
    pool_qty = pool_cost = 0.0
    events = sorted([(b.day, 0, b) for b in buys] + [(s.day, 1, s) for s in sells],
                    key=lambda e: (e[0], e[1]))
    for _, kind, lot in events:
        if kind == 0 and lot.left > 0:
            pool_qty += lot.left
            pool_cost += lot.left * lot.unit
        elif kind == 1 and lot.left > 0:
            q = min(lot.left, pool_qty)
            cost = pool_cost * q / pool_qty if pool_qty > 0 else 0.0
            pool_qty -= q
            pool_cost -= cost
            out[lot.day].cost += cost
            out[lot.day].matches.append(f"s104 {q:g}")
    return [out[d] for d in sorted(out)]


def all_disposals(txns: List[Txn]) -> List[Disposal]:
    by_sym: Dict[str, List[Txn]] = defaultdict(list)
    for t in txns:
        by_sym[t.symbol].append(t)
    out: List[Disposal] = []
    for sym_txns in by_sym.values():
        out.extend(disposals_for_symbol(sym_txns))
    return sorted(out, key=lambda d: d.day)


def pool_cost_basis(txns: List[Txn], symbol: str) -> tuple[float, float]:
    """(quantity, total allowable cost) currently held, after all matching."""
    sym = [t for t in txns if t.symbol == symbol]
    held = sum(t.quantity if t.side == "BUY" else -t.quantity for t in sym)
    if held <= 1e-9:
        return 0.0, 0.0
    bought_cost = sum(t.quantity * t.price + t.fees for t in sym if t.side == "BUY")
    used_cost = sum(d.cost for d in disposals_for_symbol(sym))
    return held, max(0.0, bought_cost - used_cost)


def estimate_sale_gain(txns: List[Txn], symbol: str, quantity: float,
                       price: float, fees: float, on: date) -> float:
    hypo = [t for t in txns if t.symbol == symbol] + [Txn(on, symbol, "SELL", quantity, price, fees)]
    return next(d.gain for d in disposals_for_symbol(hypo) if d.day == on)


@dataclass
class TaxPosition:
    tax_year: int
    realised_gains: float
    realised_losses: float
    disposal_proceeds: float
    allowance: float
    dividends: float
    dividend_allowance: float
    cgt_rate: float
    div_rate: float

    @property
    def net_gains(self) -> float:
        return self.realised_gains - self.realised_losses

    @property
    def allowance_remaining(self) -> float:
        return max(0.0, self.allowance - max(0.0, self.net_gains))

    @property
    def estimated_cgt(self) -> float:
        return max(0.0, self.net_gains - self.allowance) * self.cgt_rate

    @property
    def dividend_allowance_remaining(self) -> float:
        return max(0.0, self.dividend_allowance - self.dividends)

    @property
    def estimated_dividend_tax(self) -> float:
        return max(0.0, self.dividends - self.dividend_allowance) * self.div_rate

    @property
    def must_report(self) -> bool:
        return self.net_gains > self.allowance or self.disposal_proceeds > SA_PROCEEDS_REPORTING_LIMIT

    @property
    def label(self) -> str:
        return f"{self.tax_year}/{str(self.tax_year + 1)[-2:]}"


def tax_position(txns: List[Txn], dividends: List[tuple[date, float]], on: date) -> TaxPosition:
    year = tax_year_of(on)
    start, end = tax_year_bounds(year)
    disp = [d for d in all_disposals(txns) if start <= d.day <= end]
    r = rates_for(year)
    return TaxPosition(
        tax_year=year,
        realised_gains=sum(d.gain for d in disp if d.gain > 0),
        realised_losses=sum(-d.gain for d in disp if d.gain < 0),
        disposal_proceeds=sum(d.proceeds for d in disp),
        allowance=r["cgt_allowance"],
        dividends=sum(a for d, a in dividends if start <= d <= end),
        dividend_allowance=r["div_allowance"],
        cgt_rate=_cgt_rate(year),
        div_rate=_div_rate(year),
    )


def to_gbp(txns, default_fx: float) -> List[Txn]:
    """Indian trades (INR, with the day's INR-per-GBP rate) as GBP trades."""
    out = []
    for t in txns:
        fx = t.fx or default_fx
        out.append(Txn(t.day, t.symbol, t.side, t.quantity, t.price / fx, t.fees / fx))
    return out
