"""
config.py
=========
Central configuration dataclass for the FTSE 100 automated trading agent.
All settings are defined here and imported by other modules.
Environment variables take precedence where specified.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


# ---------------------------------------------------------------------------
# Docker-aware path detection
# ---------------------------------------------------------------------------
# When running inside the Docker container the working directory is /app.
# Mount points are /app/logs and /app/data.
# When running locally (outside Docker), use relative paths.
_IN_DOCKER: bool = os.path.exists("/app")

_LOG_FILE: str = "/app/logs/agent.log" if _IN_DOCKER else "trading_agent/agent.log"
_TRADES_CSV: str = "/app/data/trades.csv" if _IN_DOCKER else "trading_agent/trades.csv"


@dataclass
class KiteConfig:
    """Zerodha Kite Connect connection settings."""

    api_key: str = field(
        default_factory=lambda: os.getenv("KITE_API_KEY", "").strip()
    )
    """Kite API Key."""

    api_secret: str = field(
        default_factory=lambda: os.getenv("KITE_API_SECRET", "").strip()
    )
    """Kite API Secret."""

    user_id: str = field(
        default_factory=lambda: os.getenv("KITE_USER_ID", "").strip()
    )
    """Kite Username."""

    password: str = field(
        default_factory=lambda: os.getenv("KITE_PASSWORD", "").strip()
    )
    """Kite Password."""

    totp_secret: str = field(
        default_factory=lambda: os.getenv("KITE_TOTP_SECRET", "").strip()
    )
    """Kite 2FA TOTP Secret Key."""

    paper_mode: bool = field(
        default_factory=lambda: os.getenv("TRADING_MODE", "paper").lower() == "paper"
    )
    """
    When True (default), the agent performs paper trading or uses safe settings.
    Set TRADING_MODE=live to execute live orders on Zerodha.
    WARNING: Real money is used — exercise caution.
    """


@dataclass
class MarketConfig:
    """NSE trading-session parameters."""

    exchange: str = "NSE"
    currency: str = "INR"
    timezone: str = "Asia/Kolkata"

    open_hour: int = 9
    open_minute: int = 15
    close_hour: int = 15
    close_minute: int = 30

    pre_market_hour: int = 9
    """Start of pre-market window (09:00 Mumbai time)."""

    eod_close_buffer_minutes: int = 15
    """Minutes before official close at which the agent triggers EOD position closure."""


@dataclass
class UniverseConfig:
    """Nifty 50 trading universe — yfinance NSE symbol format."""

    tickers: List[str] = field(default_factory=lambda: [
        "RELIANCE.NS",   # Reliance Industries
        "TCS.NS",        # Tata Consultancy Services
        "HDFCBANK.NS",   # HDFC Bank
        "INFY.NS",       # Infosys
        "ICICIBANK.NS",  # ICICI Bank
        "HINDUNILVR.NS", # Hindustan Unilever
        "ITC.NS",        # ITC
        "SBIN.NS",       # State Bank of India
        "BHARTIARTL.NS", # Bharti Airtel
        "AXISBANK.NS",   # Axis Bank
        "LT.NS",         # Larsen & Toubro
        "KOTAKBANK.NS",  # Kotak Mahindra Bank
        "SUNPHARMA.NS",  # Sun Pharmaceutical
        "M&M.NS",        # Mahindra & Mahindra
        "ULTRACEMCO.NS", # UltraTech Cement
        "HCLTECH.NS",    # HCL Technologies
        "LTIM.NS",       # LTIMindtree
        "ASIANPAINT.NS", # Asian Paints
        "BAJFINANCE.NS", # Bajaj Finance
        "MARUTI.NS",     # Maruti Suzuki
    ])


@dataclass
class RiskConfig:
    """Position-level and portfolio-level risk controls."""

    max_position_size_pct: float = 0.05
    """Maximum fraction of portfolio NAV allocated to any single stock (5%)."""

    max_daily_loss_pct: float = 0.02
    """Agent stops trading for the day if daily P&L falls below –2% of start NAV."""

    stop_loss_pct: float = 0.02
    """Per-trade stop-loss distance from entry price (2%)."""

    take_profit_pct: float = 0.04
    """Per-trade take-profit distance from entry price (4%)."""

    max_open_positions: int = 5
    """Hard cap on simultaneous open equity positions."""

    allow_short_selling: bool = False
    """When False (default), the agent will never place a SELL order
    that would create a short position."""

    trailing_stop_pct: float = 0.015
    """Trailing stop distance from peak price (1.5%)."""


@dataclass
class WalletConfig:
    """Wallet-aware capital management settings."""

    max_deploy_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_DEPLOY_PCT", "0.5"))
    )
    """
    Maximum fraction of total portfolio NAV the agent is allowed to deploy
    into open positions at any one time.
    """

    daily_spend_cap: float = field(
        default_factory=lambda: float(os.getenv("DAILY_SPEND_CAP", "999999"))
    )
    """
    Maximum total capital deployed in new BUY orders per trading day (₹).
    """

    reinvest_profits: bool = True
    """
    When True, profits from closed positions are added back to the
    available daily spend budget.
    """

    min_trade_value: float = 1000.0
    """
    Minimum order value in INR (₹). Orders below this are skipped.
    """

    reserve_pct: float = 0.10
    """
    Fraction of available cash always kept in reserve (10%).
    """


@dataclass
class TrendConfig:
    """Technical-indicator periods used by TrendEngine."""

    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0

    ema_short: int = 9
    ema_long: int = 21

    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    atr_period: int = 14


@dataclass
class SentimentConfig:
    """Sentiment-engine weights and score bounds."""

    sentiment_weight: float = 0.3
    """Fraction of the combined signal contributed by sentiment (30%)."""

    trend_weight: float = 0.7
    """Fraction of the combined signal contributed by technical trend (70%)."""

    min_sentiment_score: float = -1.0
    max_sentiment_score: float = 1.0

    cache_ttl_seconds: int = 900
    """Seconds to cache a sentiment result before re-fetching (15 minutes)."""


@dataclass
class SignalConfig:
    """Decision-engine thresholds."""

    buy_threshold: float = 0.6
    """Combined score must be ≥ this value to trigger a BUY signal."""

    sell_threshold: float = -0.4
    """Combined score must be ≤ this value to trigger a SELL signal."""


@dataclass
class AgentConfig:
    """Top-level agent orchestration settings."""

    loop_interval_seconds: int = 60
    """Frequency at which the agent scans the full ticker universe."""

    log_file: str = field(default_factory=lambda: _LOG_FILE)
    """Path for the rotating file log handler."""

    trades_csv: str = field(default_factory=lambda: _TRADES_CSV)
    """Path for the trade-history CSV written by PortfolioTracker."""


@dataclass
class AIConfig:
    """AI Validation settings."""

    enabled: bool = field(
        default_factory=lambda: str(os.getenv("AI_VALIDATION_ENABLED", "false")).lower() == "true"
    )
    """Whether to run AI validation on trade decisions."""

    model: str = "gemini-2.5-flash"
    """Gemini model to use (e.g. gemini-2.5-flash, gemini-2.5-pro)."""

    validate_sells: bool = False
    """If True, also validate SELL decisions. If False, only validate BUYs."""


@dataclass
class Config:
    """Master configuration object."""

    kite: KiteConfig = field(default_factory=KiteConfig)
    market: MarketConfig = field(default_factory=MarketConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    wallet: WalletConfig = field(default_factory=WalletConfig)
    trend: TrendConfig = field(default_factory=TrendConfig)
    sentiment: SentimentConfig = field(default_factory=SentimentConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    ai: AIConfig = field(default_factory=AIConfig)

    # ---------- External API keys (read from environment) ----------
    eod_api_key: str = field(default_factory=lambda: os.getenv("EOD_API_KEY", ""))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))

    def __post_init__(self) -> None:
        """Validate critical settings at construction time."""
        assert 0 < self.risk.max_position_size_pct <= 1, (
            "max_position_size_pct must be in (0, 1]"
        )
        assert 0 < self.risk.max_daily_loss_pct <= 1, (
            "max_daily_loss_pct must be in (0, 1]"
        )
        assert self.sentiment.sentiment_weight + self.sentiment.trend_weight == 1.0, (
            "sentiment_weight + trend_weight must equal 1.0"
        )
        assert self.signal.buy_threshold > 0, "buy_threshold must be positive"
        assert self.signal.sell_threshold < 0, "sell_threshold must be negative"


# ---------------------------------------------------------------------------
# Module-level singleton — import directly from config for convenience.
# ---------------------------------------------------------------------------
config: Config = Config()


