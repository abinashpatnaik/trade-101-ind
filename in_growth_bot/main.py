"""Scheduler / entry point.

    python -m in_growth_bot.main            # run forever (container default)
    python -m in_growth_bot.main run-once   # one investing cycle now
    python -m in_growth_bot.main report     # build + send the weekly report now
    python -m in_growth_bot.main train      # retrain the ML model now
    python -m in_growth_bot.main check      # read-only Zerodha connection test (no orders)
    python -m in_growth_bot.main plan       # dry run of today's cycle: prints orders, sends none
    python -m in_growth_bot.main status     # ledger, recent decisions, model state
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from . import market_data, ml_model, report
from . import universe as U
from .broker import KiteBroker, SimBroker, make_broker
from .config import settings
from .ledger import Ledger
from .planner import Planner, fees
from .research import run_research, top_candidates
from .tax import Txn, tax_position

logger = logging.getLogger("in_growth_bot")
TZ = ZoneInfo(settings.timezone)
RESERVED_FILE = "in_growth_reserved.json"


def _nse_open(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    try:
        import pandas_market_calendars as mcal
        return not mcal.get_calendar("XNSE").valid_days(d, d).empty
    except Exception:
        return True


def _record_contributions(ledger: Ledger, broker, today: date) -> None:
    """Zerodha has no deposit history API, so the ledger books the monthly
    contribution on its due day. In live mode the plan is still capped at the
    cash Zerodha actually shows, so nothing is bought before the money lands."""
    if settings.starting_cash > 0 and not ledger.get_state("initial_funded"):
        ledger.record_cash_flow(today, settings.starting_cash, "contribution", "initial")
        ledger.set_state("initial_funded", True)
    if today.day >= settings.contribution_day:
        due = today.replace(day=settings.contribution_day)
        if ledger.record_cash_flow(due, settings.monthly_contribution, "contribution", "monthly"):
            ledger.log_decision(today, f"Monthly contribution Rs{settings.monthly_contribution:,.0f} booked"
                                       + (" (transfer it into Zerodha)." if settings.uses_broker else "."))


def _check_positions(ledger: Ledger, broker, today: date) -> None:
    """The account also holds the intraday agent's and your own shares, so
    only a shortfall against this bot's ledger is a problem."""
    actual = broker.positions()
    if actual is None:
        return
    for t, q in ledger.holdings().items():
        if actual.get(t, 0.0) + 1e-6 < q:
            ledger.log_decision(today, f"WARNING: Zerodha holds {actual.get(t, 0.0):g} {t} but this bot's ledger "
                                       f"says {q:g}. Was it sold by hand or by another bot? Please reconcile.")


def write_reservation(ledger: Ledger) -> None:
    """Tell the intraday agent which cash and shares belong to this bot."""
    if not settings.uses_broker:
        return
    os.makedirs(settings.shared_dir, exist_ok=True)
    data = {"updated": datetime.now(TZ).isoformat(timespec="seconds"),
            "cash": max(0.0, round(ledger.cash(), 2)),
            "holdings": {t: q for t, q in ledger.holdings().items()}}
    path = os.path.join(settings.shared_dir, RESERVED_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def _untradable(broker, holdings) -> set:
    """Pool names Zerodha can't quote are skipped (unless already held)."""
    if not settings.uses_broker or not hasattr(broker, "tradable"):
        return set()
    pool = [a.ticker for a in U.pool()]
    ok = broker.tradable(pool)
    if ok is None:
        return set()
    blocked = set(pool) - ok - set(holdings)
    if blocked:
        logger.warning("Not buying (not found on Zerodha): %s", ", ".join(sorted(blocked)))
    return blocked


def run_cycle(today: date, ledger: Ledger, broker=None):
    broker = broker or make_broker()
    if settings.uses_broker and not broker.ready():
        logger.error("Zerodha not reachable/authenticated — skipping today's cycle.")
        return
    holdings = ledger.holdings()
    closes = market_data.history(U.data_tickers() + list(holdings), period="2y")
    if closes.empty:
        logger.error("No market data — skipping.")
        return
    prices = market_data.latest_prices(closes)
    fx = market_data.fx_rate(closes)

    _record_contributions(ledger, broker, today)
    write_reservation(ledger)
    _check_positions(ledger, broker, today)

    txns = ledger.txns()
    blocked = _untradable(broker, holdings)
    prices = {t: p for t, p in prices.items() if t not in blocked}
    research = run_research(closes, holdings, exclude=blocked)
    tax = tax_position(txns, today)

    quarter = f"{today.year}Q{(today.month - 1) // 3 + 1}"
    rebalance_due = today.month in (1, 4, 7, 10) and ledger.get_state("last_rebalance") != quarter
    paused = ledger.get_state("paused_since")
    cash = ledger.cash()
    broker_cash = broker.cash()
    if broker_cash is not None:
        if broker_cash + 1 < cash:
            ledger.log_decision(today, f"Zerodha shows Rs{broker_cash:,.0f} available, less than this bot's "
                                       f"Rs{cash:,.0f}: has this month's transfer arrived?")
        cash = min(cash, broker_cash)
    elif settings.uses_broker:
        cash = 0.0
        ledger.log_decision(today, "Couldn't read Zerodha's cash balance; no purchases today.")
    rebuy = ledger.get_state("rebuy", {}) or {}
    plan = Planner(today, research, holdings, prices, cash, txns, tax,
                   {t: ledger.first_buy_day(t) for t in holdings},
                   date.fromisoformat(paused) if paused else None, rebalance_due, rebuy).run()

    for note in plan.notes:
        ledger.log_decision(today, note)
    before = ledger.get_state("held_at_loss", {}) or {}
    for t, why in plan.held_at_loss.items():
        if t not in before:
            ledger.log_decision(today, f"Not selling {t}: {why}.")
    ledger.set_state("held_at_loss", plan.held_at_loss)
    pending = dict(plan.rebuy)
    for order in sorted(plan.orders, key=lambda o: o.side != "SELL"):
        cost = order.value + fees(order.ticker, "BUY", order.value)
        if order.side == "BUY" and cost > ledger.cash() + 1e-6 and not settings.uses_broker:
            ledger.log_decision(today, f"Skipped BUY {order.ticker}: insufficient cash.")
            continue
        fill = broker.execute(order)
        if not fill:
            ledger.log_decision(today, f"FAILED {order.side} {order.quantity:g} {order.ticker} — re-planned next run.")
            if order.reason.startswith("Buying back"):
                pending[order.ticker] = pending.get(order.ticker, 0.0) + order.quantity
            elif order.side == "SELL" and order.ticker in pending:
                pending.pop(order.ticker)   # harvest sale didn't happen: nothing to buy back
            continue
        price, fee, oid, qty = fill
        ledger.record_txn(Txn(today, order.ticker, order.side, qty, price, fee, fx), order.reason, oid)
        ledger.log_decision(today, f"{order.side} {qty:g} {order.ticker} @ Rs{price:,.2f}: {order.reason}")
        if order.side == "SELL" and order.ticker in pending:
            pending[order.ticker] = qty   # buy back what actually sold
        if order.reason.startswith("Buying back") and qty < order.quantity:
            pending[order.ticker] = pending.get(order.ticker, 0.0) + order.quantity - qty

    if rebalance_due:
        ledger.set_state("last_rebalance", quarter)
    ledger.set_state("rebuy", pending)
    ledger.set_state("paused_since", plan.paused_since.isoformat() if plan.paused_since else None)
    ledger.set_state("targets", plan.targets)
    write_reservation(ledger)
    nav = ledger.cash() + sum(q * prices.get(t, 0.0) for t, q in ledger.holdings().items())
    ledger.record_nav(today, nav, ledger.cash())
    logger.info("Cycle done: %d orders, NAV Rs%.2f", len(plan.orders), nav)
    return research, plan


class _DryRunBroker:
    """Reads from the real broker; 'fills' orders on paper and never sends them."""

    def __init__(self, inner) -> None:
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def execute(self, order):
        return SimBroker().execute(order)


def dry_run(today: date) -> int:
    """Full research + planning cycle against a throwaway copy of the ledger.
    Places no orders and leaves the real ledger and the ring-fence untouched."""
    real = Ledger()
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "dry_run.db")
        if os.path.exists(real.path):
            shutil.copy(real.path, copy)
        ledger = Ledger(path=copy, mode=real.mode)
        shared = settings.shared_dir
        settings.shared_dir = tmp   # don't rewrite the real ring-fence file
        try:
            out = run_cycle(today, ledger, broker=_DryRunBroker(make_broker()))
        finally:
            settings.shared_dir = shared
        if not out:
            print("DRY RUN: cycle skipped (see log above)")
            return 1
        research, plan = out
        print(f"DRY RUN {today.isoformat()} — {settings.mode_label}. No orders sent.")
        print(f"Market data: {len(research.features)} shares with features; regime "
              f"{'RISK-ON' if research.risk_on else 'RISK-OFF'}")
        model = ml_model.load_report()
        print("ML: " + (f"AUC {model.auc:.3f}, {'ACTIVE' if model.active else 'not used (below quality bar)'}"
                        if model else "no model trained yet"))
        print(f"News sentiment fetched for {len(research.sentiment)} name(s)")
        print("Top ranked: " + ", ".join(f"{t} {research.scores[t]:+.2f}" for t in top_candidates(research)))
        print("Targets: " + ", ".join(f"{t} {w:.0%}" for t, w in sorted(plan.targets.items(), key=lambda kv: -kv[1])))
        for o in plan.orders:
            print(f"WOULD {o.side} {o.quantity:g} {o.ticker} (~Rs{o.value:,.0f}): {o.reason}")
        if not plan.orders:
            print("WOULD place no orders")
        for n in plan.notes:
            print(f"NOTE {n}")
        for t, why in plan.held_at_loss.items():
            print(f"HOLDING {t} (not sold below cost): {why}")
        print(f"Cash available to the plan: Rs{ledger.cash():,.2f} (after simulated fills)")
    return 0


def status() -> int:
    ledger = Ledger()
    today = datetime.now(TZ).date()
    print(f"Mode {settings.mode}: {settings.mode_label}")
    print(f"Last cycle: {ledger.get_state('last_run')}; last report: {ledger.get_state('last_report')}; "
          f"last training: {ledger.get_state('last_train')}")
    model = ml_model.load_report()
    print("ML: " + (f"AUC {model.auc:.3f}, {'ACTIVE' if model.active else 'not used'}, trained {model.trained_at}"
                    if model else "no model yet"))
    print(f"Contributed Rs{ledger.net_contributions():,.2f}; ledger cash Rs{ledger.cash():,.2f}")
    holdings = ledger.holdings()
    print("Holdings: " + (", ".join(f"{t} {q:g}" for t, q in holdings.items()) or "none"))
    nav = ledger.nav_history()
    if nav:
        print(f"Latest NAV Rs{nav[-1]['nav']:,.2f} on {nav[-1]['day']}")
    for d in ledger.decisions(today - timedelta(days=7))[-25:]:
        print(f"{d['day']}  {d['text']}")
    return 0


def send_report(today: date, ledger: Ledger, echo: bool = False) -> bool:
    holdings = ledger.holdings()
    closes = market_data.history(U.data_tickers() + list(holdings), period="2y")
    prices = market_data.latest_prices(closes)
    research = run_research(closes, holdings)
    rep = report.build(today, ledger, prices, ledger.get_state("targets", {}), research,
                       ml_model.load_report(), closes)
    path = os.path.join(settings.data_dir, "reports", f"weekly_{today.isoformat()}.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(rep["text"])
    if echo:
        print(rep["text"])
    sent = report.send(rep)
    logger.info("Weekly report written to %s; emailed: %s", path, "yes" if sent else "NO")
    return sent


def check() -> int:
    """Read-only Zerodha smoke test. Places no orders, whatever the mode."""
    print(f"Bot mode: {settings.mode_label}")
    b = KiteBroker()
    if not b.api_key:
        print("FAIL: KITE_API_KEY is not set")
        return 1
    if not b.ready():
        print("FAIL: couldn't authenticate with Zerodha (see log above)")
        return 1
    cash = b.cash()
    print("OK   authenticated; available cash " + (f"Rs{cash:,.2f}" if cash is not None else "unknown"))
    pool = [a.ticker for a in U.pool()]
    ok = b.tradable(pool)
    if ok is None:
        print("WARN couldn't look up instruments (quotes may need the paid Kite Connect plan); "
              "orders will be priced from Yahoo instead")
    else:
        for t in pool:
            print(f"{'OK  ' if t in ok else 'MISS'} {t:<15} -> NSE:{U.ALL[t].symbol}")
    positions = b.positions()
    mine = Ledger().holdings()
    print(f"OK   {len(positions or {})} delivery holding(s) in the account; this bot's ledger owns: "
          f"{mine or 'none'}")
    for t, q in mine.items():
        if (positions or {}).get(t, 0.0) + 1e-6 < q:
            print(f"WARN Zerodha holds {(positions or {}).get(t, 0.0):g} {t}, ledger says {q:g}")
    print("Done — no orders were placed.")
    return 0


def train() -> None:
    closes = market_data.history([a.ticker for a in U.pool()], period="10y")
    ml_model.train(closes)


def _cycle_due(now: datetime, last_run: Optional[str]) -> bool:
    return (_nse_open(now.date())
            and (settings.run_hour, settings.run_minute) <= (now.hour, now.minute) < (settings.run_until_hour, 0)
            and last_run != now.date().isoformat())


def loop() -> None:
    ledger = Ledger()
    logger.info("India growth bot started: %s", settings.mode_label)
    write_reservation(ledger)
    while True:
        now = datetime.now(TZ)
        today = now.date()
        week = f"{now.isocalendar()[0]}W{now.isocalendar()[1]}"
        try:
            if _cycle_due(now, ledger.get_state("last_run")):
                # Marked before running: a cycle that fails part-way (after
                # some orders went through) must not be retried the same day.
                ledger.set_state("last_run", today.isoformat())
                run_cycle(today, ledger)
            if (now.weekday() == settings.report_weekday and now.hour >= settings.report_hour
                    and ledger.get_state("last_report") != week):
                send_report(today, ledger)
                ledger.set_state("last_report", week)
            if (now.weekday() == settings.retrain_weekday or not ml_model.load_report()) \
                    and ledger.get_state("last_train") != week:
                train()
                ledger.set_state("last_train", week)
        except Exception:
            logger.exception("Scheduled job failed; will retry")
        time.sleep(300)


def main(argv: list) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cmd = argv[1] if len(argv) > 1 else "loop"
    today = datetime.now(TZ).date()
    if cmd == "run-once":
        run_cycle(today, Ledger())
    elif cmd == "report":
        sys.exit(0 if send_report(today, Ledger(), echo=True) else 1)
    elif cmd == "train":
        train()
    elif cmd == "check":
        sys.exit(check())
    elif cmd == "plan":
        sys.exit(dry_run(today))
    elif cmd == "status":
        sys.exit(status())
    elif cmd == "loop":
        loop()
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv)
