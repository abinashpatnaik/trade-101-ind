"""Turns research + current holdings into a small set of investing orders.

Pure logic (no network/broker), so it is fully unit-testable.

Each run:
  1. Portfolio: the best-ranked IN_POSITIONS shares, at most
     IN_MAX_PER_SECTOR per sector, equal weights tilted by signal strength.
  2. Sells, only for: a crash (down >35% from the 1-year high and below the
     200-day average; sold even at a loss), a holding that fell below rank
     IN_EXIT_RANK after IN_MIN_HOLD_DAYS (365: long-term for tax), quarterly
     drift rebalancing, and the Feb-Mar harvest of the Rs1.25 lakh long-term
     gains exemption (sold, bought back the next trading day). Apart from a
     crash, nothing is sold below its cost, and a sale whose Indian tax would
     exceed IN_MAX_SALE_TAX_PCT of proceeds is deferred.
  3. Buys: new money goes to the most underweight holdings, in orders of at
     least IN_MIN_ORDER_VALUE (NRO brokerage is capped at Rs50 an order) and
     whole shares.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from . import universe as U
from .config import settings
from .tax import TaxPosition, Txn, cost_basis, estimate_sale, in_harvest_window, long_term_qty

MAX_SALE_TAX_PCT = 0.02


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
    rebuy: Dict[str, float] = field(default_factory=dict)        # harvested today, buy back next run


def fees(ticker: str, side: str, value: float) -> float:
    """Zerodha NRO delivery charges for one order, INR."""
    if value <= 0:
        return 0.0
    s = settings
    asset = U.ALL.get(ticker)
    etf = bool(asset and asset.kind == "etf")
    brokerage = min(s.brokerage_pct * value, s.brokerage_cap)
    exchange, sebi = s.exchange_pct * value, s.sebi_pct * value
    if side == "BUY":
        stt, stamp, dp = (0.0 if etf else s.stt_pct * value), s.stamp_pct * value, 0.0
    else:
        stt, stamp, dp = (s.etf_stt_sell_pct if etf else s.stt_pct) * value, 0.0, s.dp_charge
    gst = s.gst_pct * (brokerage + exchange + sebi + dp)
    return round(brokerage + exchange + sebi + stt + stamp + dp + gst, 2)


def round_qty(q: float) -> float:
    """Whole shares only on NSE."""
    return float(math.floor(q + 1e-9)) if q > 0 else 0.0


def compose(mom: Optional[float], ml: Optional[float], sent: Optional[float]) -> float:
    parts = [(settings.w_momentum, mom), (settings.w_ml, ml), (settings.w_sentiment, sent)]
    parts = [(w, v) for w, v in parts if v is not None]
    total = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / total if total else 0.0


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def _investable(t: str) -> bool:
    a = U.ALL.get(t)
    return bool(a and a.kind in ("stock", "etf"))


class Planner:
    def __init__(self, today: date, research: Research, holdings: Dict[str, float],
                 prices: Dict[str, float], cash: float, txns: List[Txn], tax: TaxPosition,
                 held_since: Dict[str, date], paused_since: Optional[date] = None,
                 rebalance_due: bool = False, rebuy: Optional[Dict[str, float]] = None) -> None:
        self.today, self.r, self.prices, self.txns = today, research, prices, txns
        self.holdings = {t: q for t, q in holdings.items() if q > 0}
        self.cash, self.tax, self.held_since = cash, tax, held_since
        self.paused_since, self.rebalance_due = paused_since, rebalance_due
        self.rebuy = {t: q for t, q in (rebuy or {}).items() if q > 0}
        self.orders: List[Order] = []
        self.notes: List[str] = []
        self.allowance_left = tax.exemption_remaining if settings.tax_aware else math.inf
        self.sold: Dict[str, float] = {}
        self.harvested: Dict[str, float] = {}
        # Holdings that would be sold but are below cost: kept, not added to.
        self.held_at_loss: Dict[str, str] = {}

    # ------------------------------------------------------------------
    def _qty(self, t: str) -> float:
        return self.holdings.get(t, 0.0) - self.sold.get(t, 0.0)

    def _value(self, t: str) -> float:
        return self._qty(t) * self.prices.get(t, 0.0)

    def total_value(self) -> float:
        return self.cash + sum(self._value(t) for t in self.holdings)

    def _held_days(self, t: str) -> int:
        start = self.held_since.get(t)
        return (self.today - start).days if start else 0

    def _sale_tax(self, d) -> float:
        """Indian tax this sale would add (STCG in full, LTCG beyond the exemption left)."""
        st = max(0.0, d.st_gain) * self.tax.stcg_rate
        lt = max(0.0, max(0.0, d.lt_gain) - self.allowance_left) * self.tax.ltcg_rate
        return (st + lt) * (1 + self.tax.cess)

    def _sell(self, t: str, qty: float, reason: str, force: bool = False, partial_ok: bool = False,
              allow_loss: bool = False) -> bool:
        """Queue a sale. Unless *allow_loss*, only above cost; unless *force*,
        only if the Indian tax on it stays small."""
        held = self._qty(t)
        qty = held if qty >= held - 1e-9 else round_qty(qty)
        if qty <= 0 or t not in self.prices:
            return False
        price = self.prices[t]
        d = estimate_sale(self.txns, t, qty, price, fees(t, "SELL", qty * price), self.today)
        if settings.sell_only_in_profit and not allow_loss and d.gain < d.cost * settings.min_sale_profit_pct:
            h, cost = cost_basis(self.txns, t)
            avg = cost / h if h else 0.0
            self.held_at_loss[t] = (f"{reason}, but Rs{price:,.2f} is below the average cost "
                                    f"of Rs{avg:,.2f}; held until it recovers")
            return False
        if settings.tax_aware and not force and self._sale_tax(d) > MAX_SALE_TAX_PCT * d.proceeds:
            if partial_ok:
                lt = long_term_qty(self.txns, t, self.today)
                if 0 < lt < qty:
                    return self._sell(t, lt, reason + " (long-term shares only)", force, False, allow_loss)
            self.notes.append(f"Deferred selling {t}: it would cost ~Rs{self._sale_tax(d):,.0f} in Indian tax "
                              f"(short-term gain Rs{max(0.0, d.st_gain):,.0f}); waiting until it's long-term.")
            return False
        self.orders.append(Order("SELL", t, qty, price, reason))
        self.sold[t] = self.sold.get(t, 0.0) + qty
        self.allowance_left = max(0.0, self.allowance_left - max(0.0, d.lt_gain))
        return True

    # ------------------------------------------------------------------
    def _portfolio(self) -> List[str]:
        feats, scores = self.r.features, self.r.scores
        cands = [a.ticker for a in U.pool() if a.ticker in feats.index]
        ranked = sorted(cands, key=lambda t: scores.get(t, -1.0), reverse=True)
        rank = {t: i + 1 for i, t in enumerate(ranked)}
        held = sorted([t for t in self.holdings if _investable(t)] +
                      [t for t in self.rebuy if t not in self.holdings and _investable(t)],
                      key=lambda t: rank.get(t, 99))
        keep: List[str] = []
        for t in held:
            if t in self.rebuy:
                keep.append(t)
                continue
            f = feats.loc[t] if t in feats.index else None
            crashed = f is not None and f["drawdown_1y"] < settings.crash_drawdown and f["dist_sma200"] < 0
            if crashed:
                self._sell(t, self._qty(t), "Crash rule: down >35% from its 1-year high and below the "
                                            "200-day average", force=True, allow_loss=True)
            elif rank.get(t, 99) > settings.exit_rank and self._held_days(t) >= settings.min_hold_days:
                sold = self._sell(t, self._qty(t), f"Rotating out: now ranked #{rank.get(t, '?')} "
                                                   f"of {len(ranked)}")
                if not sold and t not in self.held_at_loss:
                    keep.append(t)   # deferred for tax: still a full member
            else:
                keep.append(t)
        per_group: Dict[str, int] = {}
        for t in keep + list(self.held_at_loss):
            g = U.ALL[t].group
            per_group[g] = per_group.get(g, 0) + 1
        for t in ranked:
            if len(keep) >= settings.positions:
                break
            if t in keep or t in self.held_at_loss or t in self.sold:
                continue
            f = feats.loc[t]
            if f["mom_6m"] <= 0 or f["dist_sma200"] <= 0 or t not in self.prices:
                continue
            g = U.ALL[t].group
            if per_group.get(g, 0) >= settings.max_per_group:
                continue
            sent = self.r.sentiment.get(t, 0.0)
            if sent <= settings.negative_news_veto:
                self.notes.append(f"Skipped {t}: strongly negative news flow ({sent:+.2f}).")
                continue
            keep.append(t)
            per_group[g] = per_group.get(g, 0) + 1
        return sorted(keep, key=lambda t: rank.get(t, 99))

    def targets(self) -> Dict[str, float]:
        keep = self._portfolio()
        raw = {t: 1 + settings.max_tilt * self.r.scores.get(t, 0.0) for t in keep}
        norm = sum(raw.values()) or 1.0
        t = {k: v / norm for k, v in raw.items()}
        # Holdings kept only because they're below cost stay at their current
        # weight (so nothing buys or trims them); the rest scale around them.
        total = self.total_value()
        frozen = {h: self._value(h) / total for h in self.held_at_loss if h not in t and total > 0}
        scale = max(0.0, 1.0 - sum(frozen.values()))
        t = {k: v * scale for k, v in t.items()}
        t.update(frozen)
        return t

    # ------------------------------------------------------------------
    def _rebalance(self, targets: Dict[str, float]) -> None:
        total = self.total_value()
        if total <= 0:
            return
        for t in list(self.holdings):
            excess_w = self._value(t) / total - targets.get(t, 0.0)
            if excess_w <= settings.rebalance_drift or t not in self.prices:
                continue
            q = (excess_w * total) / self.prices[t]
            self._sell(t, q, f"Quarterly rebalance: {t} is {excess_w:.0%} over target", partial_ok=True)

    def _harvest(self) -> None:
        """Feb-Mar: realise long-term gains inside the yearly exemption, then
        buy the same shares back next run, resetting their cost upward."""
        if not (settings.tax_aware and settings.harvest_exemption and in_harvest_window(self.today)):
            return
        for t in sorted(self.holdings, key=lambda t: -self._value(t)):
            if self.allowance_left < 10_000 or t in self.sold or t not in self.prices:
                continue
            price = self.prices[t]
            lt = long_term_qty(self.txns, t, self.today)
            if lt <= 0:
                continue
            d = estimate_sale(self.txns, t, lt, price, fees(t, "SELL", lt * price), self.today)
            if d.lt_gain <= 0:
                continue
            qty = lt if d.lt_gain <= self.allowance_left else round_qty(lt * self.allowance_left / d.lt_gain)
            if qty <= 0:
                continue
            d = estimate_sale(self.txns, t, qty, price, fees(t, "SELL", qty * price), self.today)
            saved = d.lt_gain * self.tax.ltcg_rate * (1 + self.tax.cess)
            cost = fees(t, "SELL", qty * price) + fees(t, "BUY", qty * price) + 2 * settings.slippage_pct * qty * price
            if d.lt_gain < 10_000 or saved < 2 * cost:
                continue
            if self._sell(t, qty, f"Tax harvest: realising Rs{d.lt_gain:,.0f} of long-term gain inside the "
                                  f"Rs{self.tax.exemption:,.0f} yearly exemption; buying back next trading day"):
                self.harvested[t] = qty

    def _buy_order(self, t: str, amount: float, reason: str) -> Optional[Order]:
        price = self.prices[t] * (1 + settings.slippage_pct)
        qty = round_qty((amount - fees(t, "BUY", amount)) / price)
        if qty <= 0:
            return None
        return Order("BUY", t, qty, self.prices[t], reason)

    def _buys(self, targets: Dict[str, float], budget: float) -> None:
        total = self.total_value()
        vals = {t: self._value(t) for t in self.holdings}
        frozen = set(self.held_at_loss)
        buyable = [t for t in targets if t not in frozen and t not in self.sold and t not in self.rebuy
                   and t in self.prices]
        left = budget
        alloc: Dict[str, float] = {}
        deficits = sorted(((targets[t] * total - vals.get(t, 0.0), t) for t in buyable), reverse=True)
        for deficit, t in deficits:
            if left < settings.min_order_value or deficit <= 0:
                break
            one_share = (self.prices[t] * (1 + settings.slippage_pct) + fees(t, "BUY", self.prices[t])) * 1.01
            amount = min(left, max(deficit, settings.min_order_value, one_share))
            if left - amount < settings.min_order_value:
                amount = left
            if amount < one_share:
                continue
            alloc[t] = amount
            left -= amount
        # Money left once every holding is at target goes to the largest target.
        if left >= settings.min_order_value and buyable:
            top = max(buyable, key=lambda t: targets[t])
            alloc[top] = alloc.get(top, 0.0) + left
        buys: List[Order] = []
        for t, amount in sorted(alloc.items(), key=lambda kv: -kv[1]):
            o = self._buy_order(t, amount, f"Invest into most underweight holding ({t}: target "
                                           f"{targets[t]:.0%}, now {vals.get(t, 0.0) / total:.0%})")
            if o:
                buys.append(o)
            else:
                self.notes.append(f"Rs{amount:,.0f} isn't enough for one share of {t} "
                                  f"(Rs{self.prices[t]:,.2f}); cash carried forward.")
        # Whole-share rounding leaves change; add extra shares where it fits.
        def cost(o: Order) -> float:
            v = o.quantity * o.est_price * (1 + settings.slippage_pct)
            return v + fees(o.ticker, "BUY", v)
        change = budget - sum(cost(o) for o in buys)
        for o in sorted(buys, key=lambda o: -targets[o.ticker]):
            while True:
                before = cost(o)
                o.quantity += 1
                if cost(o) - before > change:
                    o.quantity -= 1
                    break
                change -= cost(o) - before
        self.orders.extend(buys)

    # ------------------------------------------------------------------
    def run(self) -> Plan:
        targets = self.targets()
        if self.rebalance_due:
            self._rebalance(targets)
        self._harvest()

        paused_since = self.paused_since
        if self.r.risk_on or settings.regime_action == "ignore":
            paused_since = None
        elif paused_since is None:
            paused_since = self.today

        # Harvest proceeds are kept for tomorrow's buy-back, not reinvested today.
        proceeds = sum(o.value - fees(o.ticker, "SELL", o.value) for o in self.orders
                       if o.side == "SELL" and o.ticker not in self.harvested)
        budget = self.cash + proceeds - settings.cash_reserve
        budget -= sum(q * self.prices.get(t, 0.0) * (1 + settings.slippage_pct) for t, q in self.harvested.items())

        # Yesterday's harvest: buy the same shares back first, whatever the regime.
        pending: Dict[str, float] = dict(self.harvested)
        for t, q in self.rebuy.items():
            if t not in self.prices:
                pending[t] = q
                continue
            cost = q * self.prices[t] * (1 + settings.slippage_pct)
            if cost + fees(t, "BUY", cost) > budget:
                q = round_qty((budget - fees(t, "BUY", budget)) / (self.prices[t] * (1 + settings.slippage_pct)))
                if q <= 0:
                    pending[t] = self.rebuy[t]
                    continue
            self.orders.append(Order("BUY", t, q, self.prices[t], "Buying back after yesterday's tax harvest"))
            budget -= q * self.prices[t] * (1 + settings.slippage_pct) + fees(t, "BUY", q * self.prices[t])

        if settings.no_new_buys:
            self.notes.append("New buys disabled (IN_NO_NEW_BUYS).")
        elif paused_since and _months_between(paused_since, self.today) < settings.max_pause_months:
            self.notes.append(f"Bear-market regime (Nifty 50 below its 200-day average) since "
                              f"{paused_since.isoformat()}: holding Rs{max(budget, 0):,.0f} in cash, "
                              f"resuming within {settings.max_pause_months} months at the latest.")
        else:
            if paused_since:
                self.notes.append("Pause limit reached: investing despite the bear regime "
                                  "(time in the market beats timing it).")
            self._buys(targets, budget)
        return Plan(self.orders, targets, self.notes, paused_since, dict(self.held_at_loss), pending)
