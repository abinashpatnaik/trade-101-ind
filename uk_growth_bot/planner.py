"""Turns research + current holdings into a small set of investing orders.

Pure logic (no network/broker), so it is fully unit-testable.

Order of operations each run:
  1. Target weights: core ETFs (base weights tilted by signals) + a satellite
     sleeve of the best-ranked growth stocks + (bear regime, derisk mode) gilts.
  2. Sells, only for: broken satellite theses, holdings that fell out of the
     top ranks after the minimum hold, quarterly drift rebalancing, and the
     Feb-Apr CGT-allowance harvest (sell -> buy the twin fund). Apart from a
     broken thesis, nothing is sold below its average cost: such a holding is
     kept, gets no new money, and is sold once it recovers. In a GIA every
     discretionary sale is also capped so realised gains stay inside the
     allowance; in an ISA none of the tax rules apply.
  3. Buys: available cash goes to the most underweight slot, in orders big
     enough that the minimum commission stays a small fraction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from . import universe as U
from .config import settings
from .tax import TaxPosition, Txn, estimate_sale_gain, in_harvest_window, pool_cost_basis, recently_sold


@dataclass
class Research:
    features: pd.DataFrame                 # index = ticker
    scores: Dict[str, float]               # composite signal in [-1, 1]
    momentum: Dict[str, float] = field(default_factory=dict)
    ml: Dict[str, float] = field(default_factory=dict)
    sentiment: Dict[str, float] = field(default_factory=dict)
    headlines: Dict[str, List[str]] = field(default_factory=dict)
    risk_on: bool = True


@dataclass
class Order:
    side: str
    ticker: str
    quantity: float
    est_price: float
    reason: str

    @property
    def value(self) -> float:
        return self.quantity * self.est_price


@dataclass
class Plan:
    orders: List[Order]
    targets: Dict[str, float]
    notes: List[str]
    paused_since: Optional[date]
    held_at_loss: Dict[str, str] = field(default_factory=dict)   # ticker -> why it is still held


def fees(ticker: str, side: str, value: float) -> float:
    if value <= 0:
        return 0.0
    commission = max(settings.commission_min, settings.commission_pct * value)
    asset = U.ALL.get(ticker)
    stamp = settings.stamp_duty_pct * value if side == "BUY" and asset and asset.stamp_duty else 0.0
    return round(commission + stamp, 2)


def round_qty(q: float) -> float:
    """Round DOWN to what the broker accepts: whole shares, or fractional."""
    if q <= 0:
        return 0.0
    if not settings.fractional:
        return float(math.floor(q + 1e-9))
    f = 10 ** settings.qty_decimals
    return math.floor(q * f + 1e-9) / f


def compose(mom: Optional[float], ml: Optional[float], sent: Optional[float]) -> float:
    parts = [(settings.w_momentum, mom), (settings.w_ml, ml), (settings.w_sentiment, sent)]
    parts = [(w, v) for w, v in parts if v is not None]
    total = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / total if total else 0.0


def _slot(ticker: str) -> str:
    return U.core_slot(ticker) or ticker


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


class Planner:
    def __init__(self, today: date, research: Research, holdings: Dict[str, float],
                 prices: Dict[str, float], cash: float, txns: List[Txn], tax: TaxPosition,
                 held_since: Dict[str, date], paused_since: Optional[date] = None,
                 rebalance_due: bool = False) -> None:
        self.today, self.r, self.prices, self.txns = today, research, prices, txns
        self.holdings = {t: q for t, q in holdings.items() if q > 0}
        self.cash, self.tax, self.held_since = cash, tax, held_since
        self.paused_since, self.rebalance_due = paused_since, rebalance_due
        self.orders: List[Order] = []
        self.notes: List[str] = []
        # In an ISA gains are tax-free, so no sale is ever capped or deferred.
        gated = settings.tax_aware and not settings.is_isa
        self.allowance_left = tax.allowance_remaining if gated else math.inf
        self.sold: Dict[str, float] = {}
        # Holdings that would be sold but are below cost: kept, not added to.
        self.held_at_loss: Dict[str, str] = {}

    # ------------------------------------------------------------------
    def _qty(self, t: str) -> float:
        return self.holdings.get(t, 0.0) - self.sold.get(t, 0.0)

    def _value(self, t: str) -> float:
        return self._qty(t) * self.prices.get(t, 0.0)

    def total_value(self) -> float:
        return self.cash + sum(self._value(t) for t in self.holdings)

    def _slot_values(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for t in self.holdings:
            out[_slot(t)] = out.get(_slot(t), 0.0) + self._value(t)
        return out

    def _blocked(self, t: str) -> bool:
        if t in self.sold:
            return True
        # HMRC's 30-day matching rule only matters outside an ISA.
        return not settings.is_isa and recently_sold(self.txns, t, self.today) is not None

    def _held_days(self, t: str) -> int:
        start = self.held_since.get(t)
        return (self.today - start).days if start else 0

    def _sale_gain(self, t: str, qty: float) -> float:
        price = self.prices[t]
        return estimate_sale_gain(self.txns, t, qty, price, fees(t, "SELL", qty * price), self.today)

    def _in_profit(self, t: str, qty: float) -> bool:
        """Would selling *qty* of *t* now beat its average cost by the margin?"""
        held, cost = pool_cost_basis(self.txns, t)
        if held <= 0 or cost <= 0:
            return True
        proceeds = qty * self.prices[t] - fees(t, "SELL", qty * self.prices[t])
        return proceeds >= qty * cost / held * (1 + settings.min_sale_profit_pct)

    def _sell(self, t: str, qty: float, reason: str, force: bool = False, partial_ok: bool = False,
              allow_loss: bool = False) -> bool:
        """Queue a sale, respecting the CGT allowance unless *force*, and
        only above average cost unless *allow_loss*."""
        held = self._qty(t)
        qty = held if qty >= held - 1e-9 else round_qty(qty)
        if qty <= 0 or t not in self.prices:
            return False
        if settings.sell_only_in_profit and not allow_loss and not self._in_profit(t, qty):
            h, cost = pool_cost_basis(self.txns, t)
            self.held_at_loss[t] = (f"{reason}, but £{self.prices[t]:,.2f} is below the average cost "
                                    f"of £{cost / h:,.2f}; held until it recovers")
            return False
        gain = self._sale_gain(t, qty)
        if gain > self.allowance_left and not force:
            if not partial_ok:
                self.notes.append(f"Deferred selling {t}: £{gain:,.0f} gain would exceed the "
                                  f"£{self.allowance_left:,.0f} CGT allowance left this tax year.")
                return False
            per_share = gain / qty
            qty = round_qty(self.allowance_left / per_share) if per_share > 0 else qty
            if qty <= 0:
                self.notes.append(f"Rebalance of {t} postponed: no CGT allowance left this tax year.")
                return False
            gain = self._sale_gain(t, qty)
            reason += " (trimmed to stay inside CGT allowance)"
        self.orders.append(Order("SELL", t, qty, self.prices[t], reason))
        self.sold[t] = self.sold.get(t, 0.0) + qty
        if gain > 0:
            self.allowance_left -= gain
        return True

    # ------------------------------------------------------------------
    def _satellite(self) -> List[str]:
        feats, scores = self.r.features, self.r.scores
        cands = [a.ticker for a in U.SATELLITE_CANDIDATES if a.ticker in feats.index]
        ranked = sorted(cands, key=lambda t: scores.get(t, -1), reverse=True)
        rank = {t: i + 1 for i, t in enumerate(ranked)}
        held = [t for t in self.holdings if U.ALL.get(t) and U.ALL[t].kind == "stock"]
        keep: List[str] = []
        for t in held:
            f = feats.loc[t] if t in feats.index else None
            broken = f is not None and f["drawdown_1y"] < -0.35 and f["dist_sma200"] < 0
            if broken:
                self._sell(t, self._qty(t), "Thesis broken: down >35% from 1y high and below 200-day trend",
                           force=True, allow_loss=True)
            elif rank.get(t, 99) > settings.satellite_exit_rank and \
                    self._held_days(t) >= settings.satellite_min_hold_days:
                sold = self._sell(t, self._qty(t), f"Rotating out: now ranked #{rank.get(t, '?')} "
                                                   f"of {len(ranked)} growth candidates")
                if not sold and t not in self.held_at_loss:
                    keep.append(t)   # deferred for tax: still a full member of the sleeve
            else:
                keep.append(t)
        for t in ranked:
            if len(keep) >= settings.max_satellite_stocks:
                break
            f = feats.loc[t]
            sent = self.r.sentiment.get(t, 0.0)
            if t in keep or t in self.held_at_loss or self._blocked(t) or f["mom_6m"] <= 0 \
                    or f["dist_sma200"] <= 0:
                continue
            if sent <= settings.negative_news_veto:
                self.notes.append(f"Skipped {t}: strongly negative news flow ({sent:+.2f}).")
                continue
            keep.append(t)
        return keep

    def _slot_held_days(self, slot: str) -> int:
        return max((self._held_days(t) for t in self.holdings if _slot(t) == slot), default=0)

    def _core(self) -> List[str]:
        """Best-ranked core funds, at most one per group, with hysteresis."""
        pool = [a for a in U.CORE_POOL if a.ticker in self.r.features.index]
        ranked = sorted(pool, key=lambda a: self.r.scores.get(a.ticker, -1.0), reverse=True)
        rank = {a.ticker: i + 1 for i, a in enumerate(ranked)}
        held = sorted({_slot(t) for t in self.holdings if U.ALL.get(t) and U.ALL[t].kind == "core_etf"},
                      key=lambda s: rank.get(s, 99))
        keep: List[str] = []
        for slot in held:
            if rank.get(slot, 99) > settings.core_exit_rank and \
                    self._slot_held_days(slot) >= settings.core_min_hold_days:
                reason = (f"Rotating core fund out: {slot} now ranked #{rank.get(slot, '?')} "
                          f"of {len(ranked)} funds")
                lines = [t for t in self.holdings if _slot(t) == slot]
                sold_all = all([self._sell(t, self._qty(t), reason) for t in lines])
                if not sold_all and not any(t in self.held_at_loss for t in lines):
                    keep.append(slot)   # deferred for tax: still a full member of the core
            else:
                keep.append(slot)
        # A fund held at a loss still occupies its group, so its replacement
        # doesn't double up on the same market.
        groups = {U.ALL[s].group for s in keep} | \
                 {U.ALL[_slot(t)].group for t in self.held_at_loss if U.ALL[_slot(t)].kind == "core_etf"}
        for a in ranked:
            if len(keep) >= settings.core_funds:
                break
            if a.ticker in keep or a.group in groups:
                continue
            keep.append(a.ticker)
            groups.add(a.group)
        return sorted(keep, key=lambda s: rank.get(s, 99))

    def targets(self) -> Dict[str, float]:
        sat = self._satellite()
        per_stock = settings.satellite_pct / max(1, settings.max_satellite_stocks)
        t: Dict[str, float] = {}
        derisk = not self.r.risk_on and settings.regime_action == "derisk"
        if derisk:
            t[U.DEFENSIVE.ticker] = settings.satellite_pct
            for s in sat:
                if s in self.holdings:
                    self._sell(s, self._qty(s), "Bear regime: moving satellite sleeve to gilts")
        else:
            for s in sat:
                t[s] = per_stock
        core_total = 1.0 - sum(t.values())
        core = self._core()
        w = settings.core_rank_weights
        raw = {s: w[min(i, len(w) - 1)] * (1 + settings.max_tilt * self.r.scores.get(s, 0.0))
               for i, s in enumerate(core)}
        norm = sum(raw.values())
        for k, v in raw.items():
            t[k] = core_total * v / norm
        # Holdings kept only because they're below cost stay at their current
        # weight (so nothing buys or trims them); the rest scale around them.
        total = self.total_value()
        frozen = {}
        for h in self.held_at_loss:
            if _slot(h) not in t and total > 0:
                frozen[_slot(h)] = frozen.get(_slot(h), 0.0) + self._value(h) / total
        scale = max(0.0, 1.0 - sum(frozen.values()))
        t = {k: v * scale for k, v in t.items()}
        t.update(frozen)
        return t

    # ------------------------------------------------------------------
    def _rebalance(self, targets: Dict[str, float]) -> None:
        total = self.total_value()
        if total <= 0:
            return
        for slot, cur in self._slot_values().items():
            excess_w = cur / total - targets.get(slot, 0.0)
            if excess_w <= settings.rebalance_drift:
                continue
            excess = excess_w * total
            force = excess_w > 2 * settings.rebalance_drift
            for t in [h for h in self.holdings if _slot(h) == slot]:
                q = min(self._qty(t), excess / self.prices[t])
                if self._sell(t, q, f"Quarterly rebalance: {slot} is {excess_w:.0%} over target",
                              force=force, partial_ok=True):
                    excess -= q * self.prices[t]
                if excess <= 0:
                    break

    def _harvest(self) -> List[Order]:
        """Use the year's CGT allowance: sell a core ETF at a gain, buy its twin."""
        buys: List[Order] = []
        if settings.is_isa or not (settings.tax_aware and settings.harvest_allowance
                                   and in_harvest_window(self.today)):
            return buys
        for t in list(self.holdings):
            asset = U.ALL.get(t)
            if self.allowance_left < 100 or not asset or asset.kind != "core_etf" or not asset.twin:
                continue
            twin = asset.twin
            if twin not in self.prices or self._blocked(twin) or t not in self.prices:
                continue
            held, cost = pool_cost_basis(self.txns, t)
            gps = self.prices[t] - cost / held if held else 0.0
            if gps <= 0:
                continue
            qty = round_qty(min(self._qty(t), self.allowance_left / gps))
            saved = qty * gps * self.tax.cgt_rate
            switch_cost = fees(t, "SELL", qty * self.prices[t]) + fees(twin, "BUY", qty * self.prices[t])
            if qty <= 0 or qty * gps < 100 or saved < 3 * switch_cost:
                continue
            # gps > 0 above, so this is always a gain; the margin check doesn't apply.
            if self._sell(t, qty, f"CGT allowance harvest: realising ~£{qty * gps:,.0f} tax-free gain",
                          allow_loss=True):
                proceeds = qty * self.prices[t] - fees(t, "SELL", qty * self.prices[t])
                bq = round_qty((proceeds - fees(twin, "BUY", proceeds)) /
                                (self.prices[twin] * (1 + settings.slippage_pct)))
                if bq > 0:
                    buys.append(Order("BUY", twin, bq, self.prices[twin],
                                      f"Harvest switch from {t} (same exposure, new cost basis)"))
        return buys

    def _ticker_for_slot(self, slot: str) -> Optional[str]:
        asset = U.ALL[slot]
        if asset.kind != "core_etf":
            return None if self._blocked(slot) else slot
        options = sorted([x for x in (slot, asset.twin) if x], key=lambda x: -self._qty(x))
        for x in options:
            if not self._blocked(x) and x in self.prices:
                return x
        return None

    def _buys(self, targets: Dict[str, float], budget: float) -> None:
        total = self.total_value()
        vals = self._slot_values()
        frozen = {_slot(h) for h in self.held_at_loss}
        buyable = {s: self._ticker_for_slot(s) for s in targets if s not in frozen}
        buyable = {s: t for s, t in buyable.items() if t}
        alloc: Dict[str, float] = {}
        left = budget
        deficits = sorted(((targets[s] * total - vals.get(s, 0.0), s) for s in buyable), reverse=True)
        for deficit, slot in deficits:
            if left < settings.min_order_value or deficit <= 0:
                break
            amount = min(left, max(deficit, settings.min_order_value))
            if left - amount < settings.min_order_value:
                amount = left
            alloc[slot] = amount
            left -= amount
        # Money left once every holding is at target (e.g. after a core
        # rotation) is spread by target weight rather than sitting in cash.
        if left >= settings.min_order_value and buyable:
            weight = sum(targets[s] for s in buyable)
            extra = {s: left * targets[s] / weight for s in buyable}
            top = max(buyable, key=lambda s: targets[s])
            for s, amount in extra.items():
                if s != top and amount + alloc.get(s, 0.0) < settings.min_order_value:
                    extra[top] += amount
                    extra[s] = 0.0
            for s, amount in extra.items():
                if amount > 0:
                    alloc[s] = alloc.get(s, 0.0) + amount
        for slot, amount in sorted(alloc.items(), key=lambda kv: -kv[1]):
            t = buyable[slot]
            price = self.prices[t] * (1 + settings.slippage_pct)
            qty = round_qty((amount - fees(t, "BUY", amount)) / price)
            if qty <= 0:
                self.notes.append(f"£{amount:,.0f} isn't enough for one share of {t} "
                                  f"(£{self.prices[t]:,.2f}); cash carried forward.")
                continue
            self.orders.append(Order("BUY", t, qty, self.prices[t],
                                     f"Invest into most underweight holding ({slot}: target "
                                     f"{targets[slot]:.0%}, now {vals.get(slot, 0.0) / total:.0%})"))

    # ------------------------------------------------------------------
    def run(self) -> Plan:
        targets = self.targets()
        if self.rebalance_due:
            self._rebalance(targets)
        harvest_buys = self._harvest()

        paused_since = self.paused_since
        if self.r.risk_on or settings.regime_action == "ignore":
            paused_since = None
        elif paused_since is None:
            paused_since = self.today

        proceeds = sum(o.value - fees(o.ticker, "SELL", o.value) for o in self.orders if o.side == "SELL")
        budget = self.cash + proceeds - settings.cash_reserve
        budget -= sum(o.value + fees(o.ticker, "BUY", o.value) for o in harvest_buys)
        self.orders.extend(harvest_buys)

        if settings.no_new_buys:
            self.notes.append("New buys disabled (UK_NO_NEW_BUYS).")
        elif paused_since and _months_between(paused_since, self.today) < settings.max_pause_months:
            self.notes.append(f"Bear-market regime (global equities below 200-day average) since "
                              f"{paused_since.isoformat()}: holding £{max(budget, 0):,.0f} in cash, "
                              f"resuming within {settings.max_pause_months} months at the latest.")
        else:
            if paused_since:
                self.notes.append("Pause limit reached: investing despite the bear regime "
                                  "(time in the market beats timing it).")
            self._buys(targets, budget)
        return Plan(self.orders, targets, self.notes, paused_since, dict(self.held_at_loss))
