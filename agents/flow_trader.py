"""
agents.flow_trader
==================
Calendar-flow agent: trades the validated MONTH-END TREASURY FLOW on the US
account. Deliberately separate from the intraday trader — different horizon,
different rules, and the intraday machinery (stops, EOD flatten, vetting,
churn caps) must never touch its position.

THE STRATEGY (validated 2026-07-22; see research/calendar_flow.py)
    Long TLT from the close of the 7th-last trading day of each month to the
    close of the 2nd-last. Mechanism: bond index funds must extend duration at
    month end as the index takes on new long paper — forced buying that
    unwinds early the next month. 24 years / 288 trades: +0.304%/trade gross,
    59.7% win, survived the full robustness battery (parameter plateau,
    walk-forward, drop-best-year, duration gradient, rest-of-month control).

DESIGN RULES (each is load-bearing):
  * LONG LEG ONLY — the account is cash and cannot short.
  * NO STOP LOSS — the tested strategy holds entry-close to exit-close.
    Adding a stop would deploy something that was never validated. Risk is
    bounded by position size (FLOW_ALLOC_PCT of NAV), not by an exit rule.
  * ENTER ONLY ON THE EXACT ENTRY DAY. A mid-window entry (agent deployed or
    restarted partway through) trades an untested partial window — skip the
    month instead. Missing an EXIT is the opposite: never hold past the
    window, so a missed exit is closed at the next opportunity.
  * The intraday trader must ignore the position: ALPACA_IGNORE_SYMBOLS=TLT
    keeps it out of position adoption, exit management and the EOD flatten.
  * Idempotent via a state file — restarts must not double-enter.

Trades are recorded in the trades DB (exit_reason FLOW_ENTRY/FLOW_EXIT) so the
dashboard's net accounting includes them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

from agents.base import BaseAgent

_IN_DOCKER = os.path.exists("/app")
_STATE_PATH = (f"/app/data/flow_state_US.json" if _IN_DOCKER
               else "data/flow_state_US.json")

FLOW_ENABLED = os.getenv("FLOW_ENABLED", "false").lower() == "true"
FLOW_SYMBOL = os.getenv("FLOW_SYMBOL", "TLT").strip().upper()
FLOW_ALLOC_PCT = float(os.getenv("FLOW_ALLOC_PCT", "0.5"))
ENTRY_DAYS_BEFORE_END = int(os.getenv("FLOW_ENTRY_DAYS_BEFORE_END", "7"))
EXIT_DAYS_BEFORE_END = int(os.getenv("FLOW_EXIT_DAYS_BEFORE_END", "1"))
# act within this many minutes of the close ("at the close" per the backtest)
ACT_WINDOW_MIN = float(os.getenv("FLOW_ACT_WINDOW_MIN", "15"))


# ---------------------------------------------------------------- pure logic
def month_days(cal, year: int, month: int) -> List[date]:
    """Trading days of one month from a pandas_market_calendars calendar."""
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    sched = cal.schedule(start_date=start.isoformat(), end_date=end.isoformat())
    return [d.date() for d in sched.index if d.date() < end]


def entry_exit_days(days: List[date],
                    entry_before: int = ENTRY_DAYS_BEFORE_END,
                    exit_before: int = EXIT_DAYS_BEFORE_END):
    """(entry_day, exit_day) for one month's trading days, or (None, None).

    Mirrors research.calendar_flow.MonthEndFlow: buy the close of
    days[-entry_before], sell the close of days[-(exit_before + 1)].
    """
    if len(days) < entry_before + 1:
        return None, None
    return days[-entry_before], days[-(exit_before + 1)]


@dataclass
class FlowState:
    holding_qty: float = 0.0
    entered_month: str = ""       # "YYYY-MM" of the last entry
    exited_month: str = ""        # "YYYY-MM" of the last exit

    @classmethod
    def load(cls, path: str = _STATE_PATH) -> "FlowState":
        try:
            with open(path) as fh:
                return cls(**json.load(fh))
        except Exception:
            return cls()

    def save(self, path: str = _STATE_PATH) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.__dict__, fh)
        os.replace(tmp, path)


def decide(today: date, state: FlowState, entry_day: Optional[date],
           exit_day: Optional[date]) -> Optional[str]:
    """
    'ENTER' | 'EXIT' | None for this session.

    Encodes the asymmetry that keeps the live strategy identical to the
    validated one: entries happen ONLY on the exact entry day (a missed or
    partial window is skipped — untested territory), while a held position
    past the exit day is closed at the very next opportunity (holding into
    month-end turn territory is equally untested, in the dangerous direction).
    """
    month_key = f"{today.year:04d}-{today.month:02d}"
    if state.holding_qty > 0:
        # Holding: the only question is "should we still be?" Exit on the
        # exit day, past it (missed session), or if the month rolled over —
        # and keep signalling EXIT until the sell actually succeeds
        # (holding_qty stays > 0 on a failed order, so this retries).
        if exit_day is None or today >= exit_day or state.entered_month != month_key:
            return "EXIT"
        return None
    if entry_day is None or exit_day is None:
        return None
    if today == entry_day and state.entered_month != month_key:
        return "ENTER"
    return None


# ---------------------------------------------------------------- the agent
class FlowTraderAgent(BaseAgent):
    """Month-end bond-flow trader (US). Sleeps 99% of the time by design."""

    name = "flow"
    tick_seconds = 60.0

    def setup(self) -> None:
        if self.market != "US":
            self.logger.warning("flow_trader is US-only; %s does nothing.", self.market)
            return
        if not FLOW_ENABLED:
            self.logger.info("FLOW_ENABLED=false — agent idles (heartbeat only).")
            return
        from alpaca_connector import AlpacaConnector
        from market_session import MarketSession
        from db import TradingDB
        import pandas_market_calendars as mcal

        self.broker = AlpacaConnector()
        self.broker.connect()
        self.session = MarketSession()
        self.db = TradingDB()
        self.cal = mcal.get_calendar(self.config.market.calendar)
        self.state = FlowState.load()
        self._log_next_dates()

    def _log_next_dates(self) -> None:
        today = datetime.now(self.session._tz).date()
        days = month_days(self.cal, today.year, today.month)
        e, x = entry_exit_days(days)
        self.logger.info(
            "Flow schedule %04d-%02d: entry=%s exit=%s | holding=%.4f %s | "
            "alloc=%.0f%% of NAV, no stop (validated spec)",
            today.year, today.month, e, x, self.state.holding_qty, FLOW_SYMBOL,
            FLOW_ALLOC_PCT * 100,
        )

    def tick(self) -> None:
        if self.market != "US" or not FLOW_ENABLED:
            return
        if not self.session.is_market_open():
            return
        if self.session.minutes_remaining() > ACT_WINDOW_MIN:
            return

        today = datetime.now(self.session._tz).date()
        days = month_days(self.cal, today.year, today.month)
        entry_day, exit_day = entry_exit_days(days)
        action = decide(today, self.state, entry_day, exit_day)
        if action == "ENTER":
            self._enter(today)
        elif action == "EXIT":
            self._exit(today)

    # ------------------------------------------------------------- orders
    def _enter(self, today: date) -> None:
        summary = self.broker.get_account_summary() or {}
        nav = float(summary.get("NetLiquidation") or 0.0)
        cash = float(summary.get("AvailableFunds") or 0.0)
        price = self.broker.get_current_price(FLOW_SYMBOL)
        if nav <= 0 or not price or price <= 0:
            self.logger.error("ENTER aborted — nav=%.2f price=%s", nav, price)
            return
        # Cash account: the intraday trader may be holding positions, so the
        # allocation must also fit in SETTLED cash (95% buffer for drift).
        budget = min(nav * FLOW_ALLOC_PCT, cash * 0.95)
        qty = round(budget / price, 4)
        if qty < 0.01:
            self.logger.warning("ENTER skipped — allocation too small (qty=%.4f).", qty)
            return
        order_id = self.broker.place_market_order(FLOW_SYMBOL, "BUY", qty)
        if not order_id:
            self.logger.error("ENTER order failed for %s x%.4f.", FLOW_SYMBOL, qty)
            return
        fill = self.broker.get_order_fill_price(order_id) or price
        self.state.holding_qty = qty
        self.state.entered_month = f"{today.year:04d}-{today.month:02d}"
        self.state.save()
        self._record(today, "BUY", qty, fill, "FLOW_ENTRY", pnl=0.0)
        self.logger.info("FLOW ENTER: %s x%.4f @ %.2f (%.0f%% of NAV %.2f)",
                         FLOW_SYMBOL, qty, fill, FLOW_ALLOC_PCT * 100, nav)

    def _exit(self, today: date) -> None:
        qty = self.state.holding_qty
        price = self.broker.get_current_price(FLOW_SYMBOL) or 0.0
        order_id = self.broker.place_market_order(FLOW_SYMBOL, "SELL", qty)
        if not order_id:
            self.logger.error("EXIT order failed for %s x%.4f — will retry next tick.",
                              FLOW_SYMBOL, qty)
            return
        fill = self.broker.get_order_fill_price(order_id) or price
        # entry avg from broker position if available, else state-era price
        pos = (self.broker.get_positions() or {}).get(FLOW_SYMBOL, {})
        avg = float(pos.get("avg_cost", 0.0)) or fill
        pnl = (fill - avg) * qty
        self.state.holding_qty = 0.0
        self.state.exited_month = f"{today.year:04d}-{today.month:02d}"
        self.state.save()
        self._record(today, "SELL", qty, fill, "FLOW_EXIT", pnl=pnl)
        self.logger.info("FLOW EXIT: %s x%.4f @ %.2f pnl=%.2f", FLOW_SYMBOL, qty, fill, pnl)

    def _record(self, today: date, action: str, qty: float, price: float,
                reason: str, pnl: float) -> None:
        try:
            mode = "paper" if os.getenv("TRADING_MODE", "paper").lower() == "paper" else "live"
            self.db.insert_trade(
                date=str(today), time=datetime.now(self.session._tz).strftime("%H:%M:%S"),
                symbol=FLOW_SYMBOL, action=action, quantity=qty, price=price,
                notional=qty * price, pnl=pnl, exit_reason=reason, mode=mode)
        except Exception as exc:
            self.logger.error("trade record failed (order is live regardless): %s", exc)


def main() -> None:
    FlowTraderAgent().run()


if __name__ == "__main__":
    main()
