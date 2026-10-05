"""Env-driven settings for the UK growth investing bot."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _f(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _i(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _b(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes")


# HMRC figures keyed by tax-year start (6 April of that year). Verify against
# gov.uk each April — these change at Budgets.
TAX_YEARS: Dict[int, Dict[str, float]] = {
    2024: {"cgt_allowance": 3000, "cgt_basic": 0.18, "cgt_higher": 0.24,
           "div_allowance": 500, "div_basic": 0.0875, "div_higher": 0.3375, "div_additional": 0.3935},
    2025: {"cgt_allowance": 3000, "cgt_basic": 0.18, "cgt_higher": 0.24,
           "div_allowance": 500, "div_basic": 0.0875, "div_higher": 0.3375, "div_additional": 0.3935},
    2026: {"cgt_allowance": 3000, "cgt_basic": 0.18, "cgt_higher": 0.24,
           "div_allowance": 500, "div_basic": 0.1075, "div_higher": 0.3575, "div_additional": 0.3935},
}


@dataclass
class Settings:
    # paper = internal simulated ledger (no broker). live = IBKR CP Gateway
    # (real or IBKR-paper account depending on which login the gateway holds).
    mode: str = field(default_factory=lambda: _env("UK_TRADING_MODE", "paper").lower())
    data_dir: str = field(default_factory=lambda: _env(
        "UK_DATA_DIR", "/app/data" if os.path.exists("/.dockerenv") else
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")))
    ibkr_gateway_url: str = field(default_factory=lambda: _env("IBKR_GATEWAY_URL", "https://ibeam:5000"))
    timezone: str = "Europe/London"

    # Contributions
    monthly_contribution: float = field(default_factory=lambda: _f("UK_MONTHLY_CONTRIBUTION", 200.0))
    contribution_day: int = field(default_factory=lambda: _i("UK_CONTRIBUTION_DAY", 1))
    paper_starting_cash: float = field(default_factory=lambda: _f("UK_PAPER_STARTING_CASH", 0.0))
    cash_reserve: float = field(default_factory=lambda: _f("UK_CASH_RESERVE", 5.0))
    # Below this an order's £3 minimum commission costs more than ~3%.
    min_order_value: float = field(default_factory=lambda: _f("UK_MIN_ORDER_VALUE", 100.0))

    # Portfolio shape: aggressive growth = all equity, concentrated tilts.
    satellite_pct: float = field(default_factory=lambda: _f("UK_SATELLITE_PCT", 0.25))
    max_satellite_stocks: int = field(default_factory=lambda: _i("UK_MAX_SATELLITE_STOCKS", 2))
    # How far signals may move a core ETF's weight, relative to its base
    # weight (0.3 = a 50% base can range 35%-65% before renormalising).
    max_tilt: float = field(default_factory=lambda: _f("UK_MAX_TILT", 0.30))
    rebalance_drift: float = field(default_factory=lambda: _f("UK_REBALANCE_DRIFT", 0.10))
    satellite_min_hold_days: int = field(default_factory=lambda: _i("UK_SATELLITE_MIN_HOLD_DAYS", 180))
    # A satellite stock is sold when it drops out of the top N ranked names.
    satellite_exit_rank: int = field(default_factory=lambda: _i("UK_SATELLITE_EXIT_RANK", 5))

    # Bear-market regime: global equity below its 200-day average.
    # "pause" parks new contributions in cash (max N months) instead of buying.
    # "derisk" additionally moves the satellite sleeve into gilts.
    regime_action: str = field(default_factory=lambda: _env("UK_REGIME_ACTION", "pause").lower())
    max_pause_months: int = field(default_factory=lambda: _i("UK_MAX_PAUSE_MONTHS", 3))

    # Signal blend (momentum is the backbone; ML only counts once validated)
    w_momentum: float = 0.6
    w_ml: float = 0.25
    w_sentiment: float = 0.15
    ml_min_auc: float = field(default_factory=lambda: _f("UK_ML_MIN_AUC", 0.55))
    ml_horizon_days: int = 63  # ~3 months: an investing horizon, not a trading one
    negative_news_veto: float = -0.5

    # IBKR UK fixed-rate commission (check your plan) + UK stamp duty (SDRT,
    # charged on UK shares/investment trusts, not on ETFs)
    commission_pct: float = field(default_factory=lambda: _f("UK_COMMISSION_PCT", 0.0005))
    commission_min: float = field(default_factory=lambda: _f("UK_COMMISSION_MIN", 3.0))
    stamp_duty_pct: float = 0.005
    slippage_pct: float = 0.001

    # Tax
    income_tax_band: str = field(default_factory=lambda: _env("UK_INCOME_TAX_BAND", "basic").lower())
    tax_aware: bool = field(default_factory=lambda: _b("UK_TAX_AWARE", True))
    # Realise gains up to the unused CGT allowance between 1 Feb and 5 Apr,
    # switching into a twin fund so HMRC's 30-day rule doesn't cancel it.
    harvest_allowance: bool = field(default_factory=lambda: _b("UK_HARVEST_ALLOWANCE", True))

    no_new_buys: bool = field(default_factory=lambda: _b("UK_NO_NEW_BUYS", False))

    # Schedule (Europe/London)
    run_hour: int = 10
    run_minute: int = 30
    report_weekday: int = 4   # Friday
    report_hour: int = 17
    retrain_weekday: int = 6  # Sunday

    def __post_init__(self) -> None:
        assert self.mode in ("paper", "live"), "UK_TRADING_MODE must be paper or live"
        assert self.income_tax_band in ("basic", "higher", "additional")
        assert self.regime_action in ("pause", "derisk", "ignore")
        assert 0 <= self.satellite_pct <= 0.5
        assert abs(self.w_momentum + self.w_ml + self.w_sentiment - 1.0) < 1e-9


settings = Settings()
