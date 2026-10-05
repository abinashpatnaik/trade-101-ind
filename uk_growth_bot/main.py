"""Scheduler / entry point.

    python -m uk_growth_bot.main            # run forever (container default)
    python -m uk_growth_bot.main run-once   # one investing cycle now
    python -m uk_growth_bot.main report     # build + send the weekly report now
    python -m uk_growth_bot.main train      # retrain the ML model now
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from . import market_data, ml_model, report
from . import universe as U
from .broker import make_broker
from .config import settings
from .ledger import Ledger
from .planner import Planner, fees
from .research import run_research
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


def _record_contributions(ledger: Ledger, broker, today: date) -> None:
    if settings.mode == "paper":
        if settings.paper_starting_cash > 0 and not ledger.get_state("initial_funded"):
            ledger.record_cash_flow(today, settings.paper_starting_cash, "contribution", "initial")
            ledger.set_state("initial_funded", True)
        if today.day >= settings.contribution_day:
            due = today.replace(day=settings.contribution_day)
            if ledger.record_cash_flow(due, settings.monthly_contribution, "contribution", "monthly"):
                ledger.log_decision(today, f"Monthly contribution £{settings.monthly_contribution:,.0f} received.")
        return
    # Live: anything IBKR holds beyond what the ledger explains is a deposit
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
            ledger.log_decision(today, f"WARNING: IBKR holds {actual.get(t, 0):g} {t}, ledger says "
                                       f"{mine.get(t, 0):g}. Manual trades are not managed — please reconcile.")


def run_cycle(today: date, ledger: Ledger) -> None:
    broker = make_broker()
    if settings.mode == "live" and not broker.ready():
        logger.error("IBKR gateway not authenticated — skipping today's cycle.")
        return
    closes = market_data.history(U.ALL.keys(), period="2y")
    if closes.empty:
        logger.error("No market data — skipping.")
        return
    prices = market_data.latest_prices(closes)

    _record_dividends(ledger, today)
    _record_contributions(ledger, broker, today)
    _check_positions(ledger, broker, today)

    holdings = ledger.holdings()
    txns = ledger.txns()
    research = run_research(closes, holdings)
    tax = tax_position(txns, ledger.dividends(), today)

    quarter = f"{today.year}Q{(today.month - 1) // 3 + 1}"
    rebalance_due = today.month in (1, 4, 7, 10) and ledger.get_state("last_rebalance") != quarter
    paused = ledger.get_state("paused_since")
    plan = Planner(today, research, holdings, prices, ledger.cash(), txns, tax,
                   {t: ledger.first_buy_day(t) for t in holdings},
                   date.fromisoformat(paused) if paused else None, rebalance_due).run()

    for note in plan.notes:
        ledger.log_decision(today, note)
    for order in sorted(plan.orders, key=lambda o: o.side != "SELL"):
        cost = order.value + fees(order.ticker, "BUY", order.value)
        if order.side == "BUY" and cost > ledger.cash() + 1e-6 and settings.mode == "paper":
            ledger.log_decision(today, f"Skipped BUY {order.ticker}: insufficient cash.")
            continue
        fill = broker.execute(order)
        if not fill:
            ledger.log_decision(today, f"FAILED {order.side} {order.quantity} {order.ticker} — will retry next run.")
            continue
        price, fee, oid = fill
        ledger.record_txn(Txn(today, order.ticker, order.side, order.quantity, price, fee), order.reason, oid)
        ledger.log_decision(today, f"{order.side} {order.quantity} {order.ticker} @ £{price:,.2f}: {order.reason}")

    if rebalance_due:
        ledger.set_state("last_rebalance", quarter)
    ledger.set_state("paused_since", plan.paused_since.isoformat() if plan.paused_since else None)
    ledger.set_state("targets", plan.targets)
    nav = ledger.cash() + sum(q * prices.get(t, 0.0) for t, q in ledger.holdings().items())
    ledger.record_nav(today, nav, ledger.cash())
    logger.info("Cycle done: %d orders, NAV £%.2f", len(plan.orders), nav)


def send_report(today: date, ledger: Ledger) -> None:
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
    report.send(rep)
    logger.info("Weekly report written to %s", path)


def train() -> None:
    closes = market_data.history(U.ALL.keys(), period="10y")
    ml_model.train(closes)


def loop() -> None:
    ledger = Ledger()
    logger.info("UK growth bot started in %s mode", settings.mode.upper())
    while True:
        now = datetime.now(TZ)
        today = now.date()
        week = f"{now.isocalendar()[0]}W{now.isocalendar()[1]}"
        try:
            if (_lse_open(today) and (now.hour, now.minute) >= (settings.run_hour, settings.run_minute)
                    and ledger.get_state("last_run") != today.isoformat()):
                run_cycle(today, ledger)
                ledger.set_state("last_run", today.isoformat())
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
        send_report(today, Ledger())
    elif cmd == "train":
        train()
    else:
        loop()


if __name__ == "__main__":
    main(sys.argv)
