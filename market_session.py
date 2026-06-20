"""
market_session.py
=================
Encapsulates LSE trading-session logic: open/close windows, bank holidays,
pre-market detection, and time-to-open calculations.

Depends on:
  - pytz            (timezone handling)
  - pandas          (date/time utilities)
  - pandas_market_calendars  (LSE bank-holiday calendar)
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, date

import pandas_market_calendars as mcal
import pytz

from config import config

logger = logging.getLogger(__name__)


class MarketSession:
    """
    Provides trading session information based on the active config profile.

    All public methods operate in the configured timezone so that
    daylight savings transitions are handled automatically.

    Usage
    -----
    >>> session = MarketSession()
    >>> if session.is_market_open():
    ...     print("Market is open — start scanning")
    """

    _OPEN_TIME = time(
        config.market.open_hour,
        config.market.open_minute,
    )
    _CLOSE_TIME = time(
        config.market.close_hour,
        config.market.close_minute,
    )
    _PRE_MARKET_START = time(config.market.pre_market_hour, 0)

    def __init__(self) -> None:
        self._tz = pytz.timezone(config.market.timezone)
        self._calendar = mcal.get_calendar(config.market.calendar)
        logger.debug("MarketSession initialised with timezone %s and calendar %s", 
                     config.market.timezone, config.market.calendar)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now_local(self) -> datetime:
        """Return the current moment as a timezone-aware datetime."""
        return datetime.now(tz=self._tz)

    def _is_trading_day(self, d: date) -> bool:
        """
        Return True if *d* is a valid trading day (excludes weekends and holidays).

        Uses pandas_market_calendars to query the official schedule.
        """
        # Query a single-day window; an empty result means no trading session.
        schedule = self._calendar.schedule(
            start_date=d.strftime("%Y-%m-%d"),
            end_date=d.strftime("%Y-%m-%d"),
        )
        return not schedule.empty

    def _next_trading_day_open(self) -> datetime:
        """
        Return the timezone-aware datetime of the next market open.
        Scans forward up to 14 calendar days to skip weekends and holidays.
        """
        now = self._now_local()
        candidate = now.date()

        # If today is a trading day but market hasn't opened yet, return today's open.
        if self._is_trading_day(candidate):
            candidate_open = self._tz.localize(
                datetime.combine(candidate, self._OPEN_TIME)
            )
            if now < candidate_open:
                return candidate_open

        # Otherwise advance to the next trading day.
        for _ in range(14):
            candidate += timedelta(days=1)
            if self._is_trading_day(candidate):
                return self._tz.localize(
                    datetime.combine(candidate, self._OPEN_TIME)
                )

        raise RuntimeError(
            "Could not find a trading day within the next 14 calendar days. "
            f"Check the pandas_market_calendars {config.market.calendar} calendar data."
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_market_open(self) -> bool:
        """
        Return True if the market is currently in its normal trading session.
        Accounts for weekends and holidays via the calendar.
        """
        now = self._now_local()
        current_time = now.time()

        # Reject weekends and bank holidays first (cheap calendar lookup).
        if not self._is_trading_day(now.date()):
            return False

        # Check the clock window.
        return self._OPEN_TIME <= current_time < self._CLOSE_TIME

    def is_pre_market(self) -> bool:
        """
        Return True during the pre-market window on a valid trading day.
        Useful for pre-market data ingestion without placing live orders.
        """
        now = self._now_local()
        current_time = now.time()

        if not self._is_trading_day(now.date()):
            return False

        return self._PRE_MARKET_START <= current_time < self._OPEN_TIME

    def seconds_to_open(self) -> float:
        """
        Return the number of seconds until the next market open.

        Returns 0 if the market is currently open.
        If in pre-market, returns seconds until today's open.
        Otherwise returns seconds until the next trading day's open.
        """
        if self.is_market_open():
            return 0.0

        now = self._now_local()
        next_open = self._next_trading_day_open()
        delta = (next_open - now).total_seconds()
        return max(0.0, delta)

    def minutes_remaining(self) -> float:
        """
        Return the number of minutes remaining in the current trading session.

        Returns 0 if the market is not open.
        """
        if not self.is_market_open():
            return 0.0

        now = self._now_local()
        close_dt = self._tz.localize(
            datetime.combine(now.date(), self._CLOSE_TIME)
        )
        delta_seconds = (close_dt - now).total_seconds()
        return max(0.0, delta_seconds / 60.0)

    def is_near_close(self) -> bool:
        """
        Return True when we are within the configured EOD buffer window
        (default: 15 minutes before close).

        The agent uses this to trigger end-of-day position closure.
        """
        remaining = self.minutes_remaining()
        return 0 < remaining <= config.market.eod_close_buffer_minutes

    def get_session_date(self) -> str:
        """
        Return today's date as an ISO-8601 string (``YYYY-MM-DD``), based on
        the local timezone.
        """
        return self._now_local().strftime("%Y-%m-%d")

    def __repr__(self) -> str:
        now = self._now_local()
        open_str = "OPEN" if self.is_market_open() else "CLOSED"
        return (
            f"<MarketSession [{open_str}] "
            f"Local={now.strftime('%Y-%m-%d %H:%M:%S %Z')}>"
        )
