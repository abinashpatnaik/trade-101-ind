"""Env-driven settings for the UK growth investing bot."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _f(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _i(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _b(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes")


def _opt_f(name: str) -> Optional[float]:
    v = os.getenv(name, "").strip()
    return float(v) if v else None


# HMRC figures keyed by tax-year start (6 April of that year). Verify against
# gov.uk each April — these change at Budgets.
TAX_YEARS: Dict[int, Dict[str, float]] = {
    2024: {"isa_allowance": 20000, "cgt_allowance": 3000, "cgt_basic": 0.18, "cgt_higher": 0.24,
           "div_allowance": 500, "div_basic": 0.0875, "div_higher": 0.3375, "div_additional": 0.3935},
    2025: {"isa_allowance": 20000, "cgt_allowance": 3000, "cgt_basic": 0.18, "cgt_higher": 0.24,
           "div_allowance": 500, "div_basic": 0.0875, "div_higher": 0.3375, "div_additional": 0.3935},
    2026: {"isa_allowance": 20000, "cgt_allowance": 3000, "cgt_basic": 0.18, "cgt_higher": 0.24,
           "div_allowance": 500, "div_basic": 0.1075, "div_higher": 0.3575, "div_additional": 0.3935},
}


@dataclass
class Settings:
    # sim   = internal simulation: no broker orders, pretend money (mimics the
    #         chosen broker's fees and fractional shares).
    # paper = the broker's own paper/practice account: real API orders, fake
    #         money. On Trading 212 this is always the demo environment.
    # live  = real money (Trading 212 also needs T212_ENV=live).
    mode: str = field(default_factory=lambda: _env("UK_TRADING_MODE", "paper").lower())
    broker: str = field(default_factory=lambda: _env("UK_BROKER", "trading212").lower())
    # isa = Stocks & Shares ISA (no UK tax at all); gia = taxable General
    # Investment Account (the CGT engine in tax.py steers every sale).
    account_type: str = field(default_factory=lambda: _env("UK_ACCOUNT_TYPE", "isa").lower())
    # demo = Trading 212 practice account (fake money, real API); live = real
    # money. Real money needs both this AND UK_TRADING_MODE=live; any other mode
    # forces demo.
    t212_env: str = field(default_factory=lambda: _env("T212_ENV", "demo").lower())
    t212_api_key: str = field(default_factory=lambda: _env("T212_API_KEY", ""))
    t212_api_secret: str = field(default_factory=lambda: _env("T212_API_SECRET", ""))
    # Fractional-share precision. 0.01 of a £1,300 ETF is a £13 step, too
    # coarse for £200 a month; the broker steps down if Trading 212 refuses it.
    qty_decimals: int = field(default_factory=lambda: _i("UK_QTY_DECIMALS", 4))
    data_dir: str = field(default_factory=lambda: _env(
        "UK_DATA_DIR", "/app/data" if os.path.exists("/.dockerenv") else
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")))
    ibkr_gateway_url: str = field(default_factory=lambda: _env("IBKR_GATEWAY_URL", "https://ibeam:5000"))
    timezone: str = "Europe/London"

    # Contributions
    monthly_contribution: float = field(default_factory=lambda: _f("UK_MONTHLY_CONTRIBUTION", 200.0))
    contribution_day: int = field(default_factory=lambda: _i("UK_CONTRIBUTION_DAY", 1))
    paper_starting_cash: float = field(default_factory=lambda: _f("UK_PAPER_STARTING_CASH", 0.0))
    # Broker-dependent defaults are filled in __post_init__ when unset.
    cash_reserve: Optional[float] = field(default_factory=lambda: _opt_f("UK_CASH_RESERVE"))
    min_order_value: Optional[float] = field(default_factory=lambda: _opt_f("UK_MIN_ORDER_VALUE"))

    # Portfolio shape: aggressive growth = all equity, concentrated tilts.
    satellite_pct: float = field(default_factory=lambda: _f("UK_SATELLITE_PCT", 0.25))
    # Core sleeve: hold the best-ranked N funds from universe.CORE_POOL (at
    # most one per group), weighted by rank. A held fund is only replaced once
    # it falls below core_exit_rank AND has been held core_min_hold_days.
    core_funds: int = field(default_factory=lambda: _i("UK_CORE_FUNDS", 3))
    core_rank_weights: tuple = (0.45, 0.30, 0.25)
    core_exit_rank: int = field(default_factory=lambda: _i("UK_CORE_EXIT_RANK", 5))
    core_min_hold_days: int = field(default_factory=lambda: _i("UK_CORE_MIN_HOLD_DAYS", 90))
    max_satellite_stocks: int = field(default_factory=lambda: _i("UK_MAX_SATELLITE_STOCKS", 2))
    # How far signals may move a core ETF's weight, relative to its base
    # weight (0.3 = a 50% base can range 35%-65% before renormalising).
    max_tilt: float = field(default_factory=lambda: _f("UK_MAX_TILT", 0.30))
    rebalance_drift: float = field(default_factory=lambda: _f("UK_REBALANCE_DRIFT", 0.10))
    satellite_min_hold_days: int = field(default_factory=lambda: _i("UK_SATELLITE_MIN_HOLD_DAYS", 180))
    # A satellite stock is sold when it drops out of the top N ranked names.
    satellite_exit_rank: int = field(default_factory=lambda: _i("UK_SATELLITE_EXIT_RANK", 5))
    # Rotations, rebalances and de-risking only sell above the average cost
    # (incl. stamp duty) plus this margin; a holding under water is kept,
    # gets no new money, and is sold once it recovers. The crash rule
    # (thesis broken) is the one sale allowed at a loss.
    sell_only_in_profit: bool = field(default_factory=lambda: _b("UK_SELL_ONLY_IN_PROFIT", True))
    min_sale_profit_pct: float = field(default_factory=lambda: _f("UK_MIN_SALE_PROFIT_PCT", 0.01))

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

    # Commission (Trading 212: none; IBKR UK fixed: 0.05%, min £3 — check
    # your plan) + UK stamp duty (SDRT, on UK shares/investment trusts, not
    # ETFs; it applies inside an ISA too).
    commission_pct: Optional[float] = field(default_factory=lambda: _opt_f("UK_COMMISSION_PCT"))
    commission_min: Optional[float] = field(default_factory=lambda: _opt_f("UK_COMMISSION_MIN"))
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
    # Cycles only start inside this window: a market order sent after the
    # 16:30 close is queued by the broker until the next open.
    run_until_hour: int = 16
    report_weekday: int = 4   # Friday
    report_hour: int = 17
    retrain_weekday: int = 6  # Sunday

    def __post_init__(self) -> None:
        assert self.mode in ("sim", "paper", "live"), "UK_TRADING_MODE must be sim, paper or live"
        assert self.broker in ("trading212", "ibkr"), "UK_BROKER must be trading212 or ibkr"
        assert self.account_type in ("isa", "gia"), "UK_ACCOUNT_TYPE must be isa or gia"
        assert self.t212_env in ("demo", "live"), "T212_ENV must be demo or live"
        if self.mode != "live":
            self.t212_env = "demo"
        free = self.broker == "trading212"
        # With no commission, small orders cost nothing extra, so every pound
        # can be put to work; with a £3 minimum, orders must be ~£100+.
        defaults = {"commission_pct": 0.0 if free else 0.0005, "commission_min": 0.0 if free else 3.0,
                    "min_order_value": 10.0 if free else 100.0, "cash_reserve": 1.0 if free else 5.0}
        for k, v in defaults.items():
            if getattr(self, k) is None:
                setattr(self, k, v)
        assert self.income_tax_band in ("basic", "higher", "additional")
        assert self.regime_action in ("pause", "derisk", "ignore")
        assert 0 <= self.satellite_pct <= 0.5
        assert abs(self.w_momentum + self.w_ml + self.w_sentiment - 1.0) < 1e-9


    @property
    def uses_broker(self) -> bool:
        return self.mode in ("paper", "live")

    @property
    def simulated_funding(self) -> bool:
        """The bot credits itself the monthly contribution: in the simulation,
        and on Trading 212's practice account, whose virtual cash isn't a deposit."""
        return self.mode == "sim" or (self.broker == "trading212" and self.t212_env == "demo")

    @property
    def mode_label(self) -> str:
        if self.mode == "sim":
            return "SIMULATION (no broker orders)"
        if self.mode == "paper" or self.t212_env == "demo":
            return f"PAPER ({'Trading 212 practice account' if self.broker == 'trading212' else 'broker paper account'})"
        return "LIVE (real money)"

    @property
    def is_isa(self) -> bool:
        return self.account_type == "isa"

    @property
    def fractional(self) -> bool:
        return self.broker == "trading212"


settings = Settings()
