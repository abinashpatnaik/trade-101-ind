"""Scheduler / entry point.

    python -m uk_growth_bot.main            # run forever (container default)
    python -m uk_growth_bot.main run-once   # one investing cycle now
    python -m uk_growth_bot.main report     # build + send the weekly report now
    python -m uk_growth_bot.main train      # retrain the ML model now
    python -m uk_growth_bot.main check      # read-only broker connection test (no orders)
    python -m uk_growth_bot.main plan       # dry run of today's cycle: prints orders, sends none
    python -m uk_growth_bot.main status     # ledger, recent decisions, model state
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from . import market_data, ml_model, report
from . import universe as U
from .broker import SimBroker, Trading212Broker, make_broker
from .config import settings
from .ledger import Ledger
from .planner import Planner, fees
from .research import run_research, top_candidates
from .tax import Txn, tax_position

logger = logging.getLogger("uk_growth_bot")
TZ = ZoneInfo(settings.timezone)


def _lse_open(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    try:
        import pandas_market_calendars as mcal
        return not mcal.get_calendar("LSE").valid_days(d, d).empty
    except Exception:
        return True


def _simulated_funding() -> bool:
    return settings.simulated_funding


def _record_contributions(ledger: Ledger, broker, today: date) -> None:
    if _simulated_funding():
        if settings.paper_starting_cash > 0 and not ledger.get_state("initial_funded"):
            ledger.record_cash_flow(today, settings.paper_starting_cash, "contribution", "initial")
            ledger.set_state("initial_funded", True)
        if today.day >= settings.contribution_day:
            due = today.replace(day=settings.contribution_day)
            if ledger.record_cash_flow(due, settings.monthly_contribution, "contribution", "monthly"):
                ledger.log_decision(today, f"Monthly contribution £{settings.monthly_contribution:,.0f} received.")
        return
    if hasattr(broker, "cash_flows"):
        # Trading 212 reports deposits, withdrawals, fees and dividends itself.
        for day, amount, kind, ref in broker.cash_flows():
            if ledger.record_cash_flow(day, amount, kind, ref):
                ledger.log_decision(today, f"Trading 212 {kind}: £{amount:,.2f} on {day.isoformat()}.")
        actual = broker.cash()
        if not ledger.get_state("opened"):
            # First live run: cash already in the ISA that the recent history
            # doesn't explain (e.g. older deposits) becomes the opening balance.
            if actual is not None and not ledger.txns() and actual - ledger.cash() >= 1.0:
                opening = round(actual - ledger.cash(), 2)
                ledger.record_cash_flow(today, opening, "contribution", "opening balance")
                ledger.log_decision(today, f"Opening balance £{opening:,.2f} taken from Trading 212.")
            ledger.set_state("opened", True)
        if actual is not None and abs(actual - ledger.cash()) >= 1.0:
            ledger.log_decision(today, f"WARNING: Trading 212 shows £{actual:,.2f} available but the ledger "
                                       f"expects £{ledger.cash():,.2f}. Was something traded by hand?")
        return
    # IBKR: anything it holds beyond what the ledger explains is a deposit
    # (dividends are booked first, so they are not mistaken for one).
    actual = broker.cash()
    if actual is None:
        return
    diff = round(actual - ledger.cash(), 2)
    if abs(diff) >= 1.0:
        kind = "contribution" if diff > 0 else "withdrawal"
        ledger.record_cash_flow(today, diff, kind, f"reconciled {today.isoformat()}")
        ledger.log_decision(today, f"Reconciled IBKR cash: {kind} of £{abs(diff):,.2f}.")


def _record_dividends(ledger: Ledger, today: date) -> None:
    for t, qty in ledger.holdings().items():
        since = ledger.first_buy_day(t)
        if since is None:
            continue
        for ex, dps in market_data.dividends_per_share(t, pd.Timestamp(since)).items():
            if ex.date() > since and ex.date() <= today:
                if ledger.record_cash_flow(ex.date(), dps * qty, "dividend", f"{t}:{ex.date()}"):
                    ledger.log_decision(today, f"Dividend from {t}: £{dps * qty:,.2f}.")


def _check_positions(ledger: Ledger, broker, today: date) -> None:
    actual = broker.positions()
    if actual is None:
        return
    mine = ledger.holdings()
    for t in set(actual) | set(mine):
        if abs(actual.get(t, 0) - mine.get(t, 0)) > 1e-6:
            ledger.log_decision(today, f"WARNING: {settings.broker} holds {actual.get(t, 0):g} {t}, ledger says "
                                       f"{mine.get(t, 0):g}. Manual trades are not managed — please reconcile.")


def _untradable(broker, tickers, holdings) -> set:
    """Assets the bot must not buy: not priced in GBP, or not offered by the
    broker. Existing holdings are kept so they can still be valued and sold."""
    blocked = set(market_data.non_gbp(tickers))
    if settings.uses_broker and settings.broker == "trading212":
        mapping = broker.instrument_map() if hasattr(broker, "instrument_map") else None
        if mapping:  # an empty map means the lookup failed; execute() refuses those orders anyway
            blocked |= {t for t in U.ALL if t not in mapping}
    blocked -= set(holdings)
    if blocked:
        logger.warning("Not buying (not GBP or not on %s): %s", settings.broker, ", ".join(sorted(blocked)))
    return blocked


def run_cycle(today: date, ledger: Ledger, broker=None):
    broker = broker or make_broker()
    if settings.uses_broker and not broker.ready():
        logger.error("%s not reachable/authenticated — skipping today's cycle.", settings.broker)
        return
    closes = market_data.history(U.ALL.keys(), period="2y")
    if closes.empty:
        logger.error("No market data — skipping.")
        return
    prices = market_data.latest_prices(closes)

    if _simulated_funding() or settings.broker != "trading212":
        _record_dividends(ledger, today)  # real Trading 212 accounts report actual dividends
    _record_contributions(ledger, broker, today)
    _check_positions(ledger, broker, today)

    holdings = ledger.holdings()
    txns = ledger.txns()
    blocked = _untradable(broker, closes.columns, holdings)
    prices = {t: p for t, p in prices.items() if t not in blocked}
    research = run_research(closes, holdings, exclude=blocked)
    tax = tax_position(txns, ledger.dividends(), today)

    quarter = f"{today.year}Q{(today.month - 1) // 3 + 1}"
    rebalance_due = today.month in (1, 4, 7, 10) and ledger.get_state("last_rebalance") != quarter
    paused = ledger.get_state("paused_since")
    cash = ledger.cash()
    broker_cash = broker.cash()
    if broker_cash is not None:
        cash = min(cash, broker_cash)  # never plan to spend money the broker doesn't show
    elif settings.uses_broker:
        cash = 0.0  # balance unknown: buy nothing today rather than guess
        ledger.log_decision(today, "Couldn't read the broker's cash balance; no purchases today.")
    plan = Planner(today, research, holdings, prices, cash, txns, tax,
                   {t: ledger.first_buy_day(t) for t in holdings},
                   date.fromisoformat(paused) if paused else None, rebalance_due).run()

    for note in plan.notes:
        ledger.log_decision(today, note)
    # Log a holding held back from a sale once, when it starts being held.
    before = ledger.get_state("held_at_loss", {}) or {}
    for t, why in plan.held_at_loss.items():
        if t not in before:
            ledger.log_decision(today, f"Not selling {t}: {why}.")
    ledger.set_state("held_at_loss", plan.held_at_loss)
    for order in sorted(plan.orders, key=lambda o: o.side != "SELL"):
        cost = order.value + fees(order.ticker, "BUY", order.value)
        if order.side == "BUY" and cost > ledger.cash() + 1e-6 and settings.mode == "sim":
            ledger.log_decision(today, f"Skipped BUY {order.ticker}: insufficient cash.")
            continue
        fill = broker.execute(order)
        if not fill:
            ledger.log_decision(today, f"FAILED {order.side} {order.quantity:g} {order.ticker} — re-planned next run.")
            continue
        price, fee, oid, qty = fill
        ledger.record_txn(Txn(today, order.ticker, order.side, qty, price, fee), order.reason, oid)
        ledger.log_decision(today, f"{order.side} {qty:g} {order.ticker} @ £{price:,.2f}: {order.reason}")

    if rebalance_due:
        ledger.set_state("last_rebalance", quarter)
    ledger.set_state("paused_since", plan.paused_since.isoformat() if plan.paused_since else None)
    ledger.set_state("targets", plan.targets)
    nav = ledger.cash() + sum(q * prices.get(t, 0.0) for t, q in ledger.holdings().items())
    ledger.record_nav(today, nav, ledger.cash())
    logger.info("Cycle done: %d orders, NAV £%.2f", len(plan.orders), nav)
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
    Places no orders and leaves the real ledger untouched."""
    real = Ledger()
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "dry_run.db")
        if os.path.exists(real.path):
            shutil.copy(real.path, copy)
        ledger = Ledger(path=copy, mode=real.mode)
        out = run_cycle(today, ledger, broker=_DryRunBroker(make_broker()))
        if not out:
            print("DRY RUN: cycle skipped (see log above)")
            return 1
        research, plan = out
        print(f"DRY RUN {today.isoformat()} — {settings.mode_label}, broker {settings.broker}. No orders sent.")
        print(f"Market data: {len(research.features)} assets with features; regime "
              f"{'RISK-ON' if research.risk_on else 'RISK-OFF'}")
        model = ml_model.load_report()
        print("ML: " + (f"AUC {model.auc:.3f}, {'ACTIVE' if model.active else 'not used (below quality bar)'}"
                        if model else "no model trained yet"))
        print(f"News sentiment fetched for {len(research.sentiment)} name(s)")
        print("Top growth candidates: " + ", ".join(
            f"{t} {research.scores[t]:+.2f}" for t in top_candidates(research)))
        print("Targets: " + ", ".join(f"{t} {w:.0%}" for t, w in sorted(plan.targets.items(), key=lambda kv: -kv[1])))
        for o in plan.orders:
            print(f"WOULD {o.side} {o.quantity:g} {o.ticker} (~£{o.value:,.2f}): {o.reason}")
        if not plan.orders:
            print("WOULD place no orders")
        for n in plan.notes:
            print(f"NOTE {n}")
        for t, why in plan.held_at_loss.items():
            print(f"HOLDING {t} (not sold below cost): {why}")
        print(f"Cash available to the plan: £{ledger.cash():,.2f} (after simulated fills)")
    return 0


def status() -> int:
    ledger = Ledger()
    today = datetime.now(TZ).date()
    print(f"Mode {settings.mode}: {settings.mode_label}; broker {settings.broker}, account {settings.account_type}")
    print(f"Last cycle: {ledger.get_state('last_run')}; last report: {ledger.get_state('last_report')}; "
          f"last training: {ledger.get_state('last_train')}")
    model = ml_model.load_report()
    print("ML: " + (f"AUC {model.auc:.3f}, {'ACTIVE' if model.active else 'not used'}, trained {model.trained_at}"
                    if model else "no model yet"))
    print(f"Contributed £{ledger.net_contributions():,.2f}; ledger cash £{ledger.cash():,.2f}")
    holdings = ledger.holdings()
    print("Holdings: " + (", ".join(f"{t} {q:g}" for t, q in holdings.items()) or "none"))
    nav = ledger.nav_history()
    if nav:
        print(f"Latest NAV £{nav[-1]['nav']:,.2f} on {nav[-1]['day']}")
    for d in ledger.decisions(today - timedelta(days=7))[-25:]:
        print(f"{d['day']}  {d['text']}")
    return 0


def send_report(today: date, ledger: Ledger, echo: bool = False) -> bool:
    closes = market_data.history(U.ALL.keys(), period="2y")
    prices = market_data.latest_prices(closes)
    research = run_research(closes, ledger.holdings())
    tax = tax_position(ledger.txns(), ledger.dividends(), today)
    rep = report.build(today, ledger, prices, ledger.get_state("targets", {}), research, tax,
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
    """Read-only Trading 212 smoke test. Places no orders, whatever the mode."""
    if settings.broker != "trading212":
        print("check supports UK_BROKER=trading212 only")
        return 2
    print(f"Trading 212 {settings.t212_env.upper()} environment "
          f"({'PRACTICE, fake money' if settings.t212_env == 'demo' else 'REAL MONEY'}); "
          f"bot mode: {settings.mode_label}")
    if not settings.t212_api_key:
        print("FAIL: T212_API_KEY is not set")
        return 1
    if not settings.t212_api_secret:
        print("WARN T212_API_SECRET is not set — only old key-only API keys work without it")
    b = Trading212Broker()
    if not b.ready():
        print("FAIL: Trading 212 rejected the key or is unreachable (see log above)")
        return 1
    cash = b.cash()
    print(f"OK   authenticated; available cash " + (f"£{cash:,.2f}" if cash is not None
                                                      else "unknown (lookup failed — see log above)"))
    mapping = b.instrument_map()
    missing = [t for t in U.ALL if t not in mapping]
    for t in U.ALL:
        print(f"{'OK  ' if t in mapping else 'MISS'} {t:<8} -> {mapping.get(t, '(not found)')}")
    positions = b.positions() or {}
    print(f"OK   {len(positions)} open position(s): {positions or 'none'}")
    flows = b.cash_flows()
    print(f"OK   {len(flows)} recent deposit/withdrawal/fee/dividend record(s)")
    for t in missing:
        options = ", ".join(f"{i.get('ticker')} ({i.get('name')}, {i.get('currencyCode')})"
                            for i in b.suggest(t)) or "no similar names"
        print(f"WARN {t} not found; the bot will skip it. Closest Trading 212 instruments: {options}. "
              f"Fix with UK_T212_TICKERS={t}=<ticker>")
    print("Done — no orders were placed.")
    return 0


def train() -> None:
    closes = market_data.history(U.ALL.keys(), period="10y")
    ml_model.train(closes)


def _cycle_due(now: datetime, last_run: Optional[str]) -> bool:
    return (_lse_open(now.date())
            and (settings.run_hour, settings.run_minute) <= (now.hour, now.minute) < (settings.run_until_hour, 0)
            and last_run != now.date().isoformat())


def loop() -> None:
    ledger = Ledger()
    logger.info("UK growth bot started: %s", settings.mode_label)
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
