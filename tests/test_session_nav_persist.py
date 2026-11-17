"""
The daily loss limit survives a restart.

The session-start NAV it measures from used to live only in memory, so a
crash or deploy after a losing morning re-based the limit on the already-down
NAV and gave the agent a fresh 2% to lose. It is now saved per market per
day, and a restarted tracker picks it up.
"""

import pytest

from config import config
from portfolio_tracker import PortfolioTracker


class _Broker:
    def __init__(self, nav):
        self.nav = nav

    def get_account_summary(self):
        return {"NetLiquidation": self.nav, "AvailableFunds": self.nav, "DailyPnL": 0.0}

    def get_positions(self):
        return {}


def _tracker(monkeypatch, tmp_path):
    t = PortfolioTracker()
    monkeypatch.setattr(t, "_dump_local_positions", lambda: None)
    monkeypatch.setattr(t, "_dump_local_summary", lambda: None)
    return t


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("TRADING_MARKET", "US")
    monkeypatch.setattr(config.agent, "trades_csv", str(tmp_path / "trades.csv"))
    monkeypatch.setattr(config.risk, "max_daily_loss_pct", 0.02)


def test_restart_keeps_the_morning_baseline(monkeypatch, tmp_path):
    first = _tracker(monkeypatch, tmp_path)
    first.update(_Broker(10_000.0))
    assert first._session_start_nav == 10_000.0

    # Down 1.5% by mid-morning, then the container restarts.
    restarted = _tracker(monkeypatch, tmp_path)
    restarted.update(_Broker(9_850.0))
    assert restarted._session_start_nav == 10_000.0
    assert not restarted.check_daily_loss_limit()

    # Another 0.6% down: the ORIGINAL 2% limit trips.
    restarted.update(_Broker(9_790.0))
    assert restarted.check_daily_loss_limit()


def test_yesterdays_baseline_is_not_reused(monkeypatch, tmp_path):
    t = _tracker(monkeypatch, tmp_path)
    t._save_session_nav("2000-01-01", 50_000.0)
    t.update(_Broker(10_000.0))
    assert t._session_start_nav == 10_000.0
