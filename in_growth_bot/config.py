"""Env-driven settings for the India growth investing bot (Zerodha, NSE)."""

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


# Indian capital-gains tax on listed shares and equity ETFs (Finance Act 2024
# rates, from 23 Jul 2024), keyed by financial-year start (1 April). Cess of 4%
# applies on top. Verify each Budget (usually 1 February).
IN_TAX_YEARS: Dict[int, Dict[str, float]] = {
    2024: {"stcg": 0.20, "ltcg": 0.125, "ltcg_exemption": 125000, "cess": 0.04},
    2025: {"stcg": 0.20, "ltcg": 0.125, "ltcg_exemption": 125000, "cess": 0.04},
    2026: {"stcg": 0.20, "ltcg": 0.125, "ltcg_exemption": 125000, "cess": 0.04},
}

# UK figures (tax year from 6 April). As a UK resident, gains on these Indian
# holdings are also within UK CGT, with credit for the Indian tax (India-UK
# DTAA). Used for the GBP estimate in the weekly report.
UK_TAX_YEARS: Dict[int, Dict[str, float]] = {
    2024: {"cgt_allowance": 3000, "cgt_basic": 0.18, "cgt_higher": 0.24,
           "div_allowance": 500, "div_basic": 0.0875, "div_higher": 0.3375, "div_additional": 0.3935},
    2025: {"cgt_allowance": 3000, "cgt_basic": 0.18, "cgt_higher": 0.24,
           "div_allowance": 500, "div_basic": 0.0875, "div_higher": 0.3375, "div_additional": 0.3935},
    2026: {"cgt_allowance": 3000, "cgt_basic": 0.18, "cgt_higher": 0.24,
           "div_allowance": 500, "div_basic": 0.1075, "div_higher": 0.3575, "div_additional": 0.3935},
}


@dataclass
class Settings:
    # sim  = internal simulation on real prices: no broker orders, pretend money.
    #        (Zerodha has no paper/practice account.)
    # live = real orders on Zerodha.
    mode: str = field(default_factory=lambda: _env("IN_TRADING_MODE", "sim").lower())
    data_dir: str = field(default_factory=lambda: _env(
        "IN_DATA_DIR", "/app/data" if os.path.exists("/.dockerenv") else
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")))
    # Shared with the intraday IN agent: its cached Kite access token, and the
    # ring-fence file telling it which cash and shares belong to this bot.
    shared_dir: str = field(default_factory=lambda: _env(
        "IN_SHARED_DIR", "/app/shared" if os.path.exists("/.dockerenv") else
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "shared")))
    timezone: str = "Asia/Kolkata"

    # Contributions (INR). Money arrives by your own transfer into Zerodha.
    monthly_contribution: float = field(default_factory=lambda: _f("IN_MONTHLY_CONTRIBUTION", 25000.0))
    contribution_day: int = field(default_factory=lambda: _i("IN_CONTRIBUTION_DAY", 1))
    starting_cash: float = field(default_factory=lambda: _f("IN_STARTING_CASH", 0.0))
    cash_reserve: float = field(default_factory=lambda: _f("IN_CASH_RESERVE", 200.0))
    # NRO brokerage is 0.5% capped at Rs50 an order, so orders below Rs10,000
    # pay the full 0.5%. A month's money goes into one or two holdings.
    min_order_value: float = field(default_factory=lambda: _f("IN_MIN_ORDER_VALUE", 10000.0))

    # Portfolio: the best-ranked N shares, at most max_per_group per sector.
    positions: int = field(default_factory=lambda: _i("IN_POSITIONS", 8))
    max_per_group: int = field(default_factory=lambda: _i("IN_MAX_PER_SECTOR", 2))
    # A holding is replaced only once it ranks below exit_rank AND has been
    # held min_hold_days (365: gains become long-term, taxed 12.5% not 20%).
    exit_rank: int = field(default_factory=lambda: _i("IN_EXIT_RANK", 15))
    min_hold_days: int = field(default_factory=lambda: _i("IN_MIN_HOLD_DAYS", 365))
    # Indian ETFs aren't UK "reporting funds": for a UK resident their gains
    # are taxed as income (up to 45%), not CGT. Off unless you opt in.
    include_etfs: bool = field(default_factory=lambda: _b("IN_INCLUDE_ETFS", False))
    max_tilt: float = field(default_factory=lambda: _f("IN_MAX_TILT", 0.30))
    rebalance_drift: float = field(default_factory=lambda: _f("IN_REBALANCE_DRIFT", 0.10))
    sell_only_in_profit: bool = field(default_factory=lambda: _b("IN_SELL_ONLY_IN_PROFIT", True))
    min_sale_profit_pct: float = field(default_factory=lambda: _f("IN_MIN_SALE_PROFIT_PCT", 0.01))
    # Crash rule: sold even at a loss.
    crash_drawdown: float = -0.35

    # Bear market (Nifty 50 below its 200-day average): pause new buying for
    # up to N months, or ignore.
    regime_action: str = field(default_factory=lambda: _env("IN_REGIME_ACTION", "pause").lower())
    max_pause_months: int = field(default_factory=lambda: _i("IN_MAX_PAUSE_MONTHS", 3))

    # Signal blend (same as the UK bot)
    w_momentum: float = 0.6
    w_ml: float = 0.25
    w_sentiment: float = 0.15
    ml_min_auc: float = field(default_factory=lambda: _f("IN_ML_MIN_AUC", 0.55))
    ml_horizon_days: int = 63
    negative_news_veto: float = -0.5

    # Zerodha NRI (NRO, non-PIS) delivery charges. Check the rate card.
    brokerage_pct: float = field(default_factory=lambda: _f("IN_BROKERAGE_PCT", 0.005))
    brokerage_cap: float = field(default_factory=lambda: _f("IN_BROKERAGE_CAP", 50.0))
    stt_pct: float = 0.001            # shares, delivery: buy and sell
    etf_stt_sell_pct: float = 0.00001  # equity ETFs: sell side only
    exchange_pct: float = 0.0000297   # NSE transaction charge
    sebi_pct: float = 0.000001        # Rs10 per crore
    stamp_pct: float = 0.00015        # buy side, delivery
    gst_pct: float = 0.18             # on brokerage + exchange + SEBI + DP
    dp_charge: float = field(default_factory=lambda: _f("IN_DP_CHARGE", 13.0))  # per scrip sold per day, pre-GST
    slippage_pct: float = 0.002
    # Limit orders are sent this far through the last price (marketable limit).
    limit_buffer_pct: float = 0.005

    # Tax
    uk_income_tax_band: str = field(default_factory=lambda: (
        _env("IN_UK_INCOME_TAX_BAND", "") or _env("UK_INCOME_TAX_BAND", "") or "basic").lower())
    tax_aware: bool = field(default_factory=lambda: _b("IN_TAX_AWARE", True))
    # In Feb-Mar, realise long-term gains up to the Rs1.25 lakh exemption and
    # buy back the next trading day (India has no wash-sale rule).
    harvest_exemption: bool = field(default_factory=lambda: _b("IN_HARVEST_EXEMPTION", True))

    no_new_buys: bool = field(default_factory=lambda: _b("IN_NO_NEW_BUYS", False))

    # Schedule (Asia/Kolkata). NSE trades 09:15-15:30.
    run_hour: int = 10
    run_minute: int = 15
    run_until_hour: int = 15
    report_weekday: int = 4   # Friday
    report_hour: int = 18
    retrain_weekday: int = 6  # Sunday

    def __post_init__(self) -> None:
        assert self.mode in ("sim", "live"), "IN_TRADING_MODE must be sim or live"
        assert self.regime_action in ("pause", "ignore"), "IN_REGIME_ACTION must be pause or ignore"
        assert self.uk_income_tax_band in ("basic", "higher", "additional")
        assert self.positions >= 1 and self.max_per_group >= 1
        assert abs(self.w_momentum + self.w_ml + self.w_sentiment - 1.0) < 1e-9

    @property
    def uses_broker(self) -> bool:
        return self.mode == "live"

    @property
    def mode_label(self) -> str:
        return "SIMULATION (no broker orders)" if self.mode == "sim" else "LIVE (real money, Zerodha)"


settings = Settings()
