"""
config.py
=========
Central configuration dataclass for the automated trading agent.
Supports multiple markets (IN, US) via the TRADING_MARKET environment variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

# ---------------------------------------------------------------------------
# Docker-aware path detection
# ---------------------------------------------------------------------------
_IN_DOCKER: bool = os.path.exists("/app")

# Detect the active market at import time
ACTIVE_MARKET = os.getenv("TRADING_MARKET", "IN").upper()

_LOG_FILE: str = f"/app/logs/agent_{ACTIVE_MARKET}.log" if _IN_DOCKER else f"trading_agent/agent_{ACTIVE_MARKET}.log"
_TRADES_CSV: str = f"/app/data/trades_{ACTIVE_MARKET}.csv" if _IN_DOCKER else f"trading_agent/trades_{ACTIVE_MARKET}.csv"


@dataclass
class KiteConfig:
    """Zerodha Kite Connect connection settings."""
    api_key: str = field(default_factory=lambda: os.getenv("KITE_API_KEY", "").strip())
    api_secret: str = field(default_factory=lambda: os.getenv("KITE_API_SECRET", "").strip())
    user_id: str = field(default_factory=lambda: os.getenv("KITE_USER_ID", "").strip())
    password: str = field(default_factory=lambda: os.getenv("KITE_PASSWORD", "").strip())
    totp_secret: str = field(default_factory=lambda: os.getenv("KITE_TOTP_SECRET", "").strip())
    paper_mode: bool = field(default_factory=lambda: os.getenv("TRADING_MODE", "paper").lower() == "paper")


@dataclass
class AlpacaConfig:
    """Alpaca US connection settings."""
    api_key: str = field(default_factory=lambda: os.getenv("APCA_API_KEY_ID", "").strip())
    api_secret: str = field(default_factory=lambda: os.getenv("APCA_API_SECRET_KEY", "").strip())
    paper_mode: bool = field(default_factory=lambda: os.getenv("TRADING_MODE", "paper").lower() == "paper")


@dataclass
class MarketConfig:
    """Trading-session parameters."""
    exchange: str
    currency: str
    timezone: str
    calendar: str
    open_hour: int
    open_minute: int
    close_hour: int
    close_minute: int
    pre_market_hour: int
    eod_close_buffer_minutes: int = 15


@dataclass
class UniverseConfig:
    """Trading universe."""
    tickers: List[str]


@dataclass
class RiskConfig:
    """Position-level and portfolio-level risk controls."""
    max_position_size_pct: float = 0.30
    max_daily_loss_pct: float = 0.02
    stop_loss_pct: float = 0.025            # 2.5% hard stop (US default, floor for ATR-dynamic)
    take_profit_pct: float = 9.99  # Effectively disabled so profits can run
    max_open_positions: int = 3
    allow_short_selling: bool = False
    trailing_stop_pct: float = field(default_factory=lambda: float(os.getenv("TRAILING_STOP_PCT", "0.015")))
    # Profit-lock trailing stop: minimum CURRENT gain to activate
    profit_lock_threshold: float = 0.005    # +0.5% (US default, floor for ATR-dynamic)
    # Base trailing gap for small gains (widest gap in the graduated table)
    trailing_gap_base: float = 0.008        # 0.8% (US default)


@dataclass
class WalletConfig:
    """Wallet-aware capital management settings."""
    max_deploy_pct: float = field(default_factory=lambda: float(os.getenv("MAX_DEPLOY_PCT", "0.5")))
    daily_spend_cap: float = field(default_factory=lambda: float(os.getenv("DAILY_SPEND_CAP", "999999")))
    reinvest_profits: bool = True
    min_trade_value: float = 1000.0  # Overridden per market
    reserve_pct: float = 0.10


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
    trend_weight: float = 0.7
    min_sentiment_score: float = -1.0
    max_sentiment_score: float = 1.0
    cache_ttl_seconds: int = 900


@dataclass
class SignalConfig:
    """Decision-engine thresholds."""
    buy_threshold: float = 0.48
    sell_threshold: float = -0.35
    ml_buy_threshold: float = 0.55


@dataclass
class AgentConfig:
    """Top-level agent orchestration settings."""
    loop_interval_seconds: int = 60
    intraday_scan_interval_minutes: int = 60
    log_file: str = field(default_factory=lambda: _LOG_FILE)
    trades_csv: str = field(default_factory=lambda: _TRADES_CSV)
    liquidate_on_shutdown: bool = field(
        default_factory=lambda: str(os.getenv("LIQUIDATE_ON_SHUTDOWN", "false")).lower() == "true"
    )
    observe_only: bool = field(
        default_factory=lambda: str(os.getenv("OBSERVE_ONLY", "false")).lower() == "true"
    )


@dataclass
class AIConfig:
    """AI Validation settings."""
    enabled: bool = field(default_factory=lambda: str(os.getenv("AI_VALIDATION_ENABLED", "false")).lower() == "true")
    model: str = "gemini-2.5-flash"
    validate_sells: bool = False
    primary_driver: bool = field(default_factory=lambda: str(os.getenv("AI_PRIMARY_DRIVER", "false")).lower() == "true")


@dataclass
class BusConfig:
    """Redis message-bus settings shared by all agents."""
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://redis:6379/0"))
    heartbeat_period_seconds: int = 30
    heartbeat_ttl_seconds: int = 90
    # New BUYs are suppressed after the bus has been unreachable this long
    # (blocklist/vetting data may be stale — fail toward not entering).
    buy_suppress_after_seconds: int = 90


@dataclass
class VettingConfig:
    """Profit-vetting agent settings (backtest screen + live-accuracy blocklist)."""
    backtest_lookback_period: str = "10d"
    backtest_interval: str = "5m"
    # Block a symbol when its simulated total return is below this threshold
    ev_threshold_pct: float = 0.0
    # Live-accuracy blocklist
    accuracy_lookback_sessions: int = 5
    accuracy_window_trades: int = 10
    min_trades_to_judge: int = 3
    min_hit_rate: float = 0.40
    consecutive_stop_losses_to_block: int = 2


@dataclass
class StrategyConfig:
    """Market-regime strategy agent settings."""
    index_symbol: str = "^NSEI"          # Overridden per market profile
    classify_interval_minutes: int = 15
    adx_trending_threshold: float = 25.0
    atr_volatile_pct: float = 0.025      # Daily ATR% above this => VOLATILE
    # Require this many consecutive agreeing reads before switching regime
    hysteresis_reads: int = 2
    # A directive older than this is ignored by the trader
    directive_stale_minutes: int = 30


@dataclass
class OrchestratorConfig:
    """Primary-agent supervision settings."""
    tick_seconds: int = 20
    train_daily_minutes_before_open: int = 90
    strategy_minutes_before_open: int = 10
    intraday_scan_interval_minutes: int = 60
    max_restarts_per_agent_per_day: int = 3
    # Suppress trader restarts for this long after session close
    # (the trader exits by design post-session; docker revives it).
    trader_restart_suppress_seconds: int = 300


@dataclass
class Config:
    """Master configuration object."""
    kite: KiteConfig
    alpaca: AlpacaConfig
    market: MarketConfig
    universe: UniverseConfig
    risk: RiskConfig
    wallet: WalletConfig
    trend: TrendConfig
    sentiment: SentimentConfig
    signal: SignalConfig
    agent: AgentConfig
    ai: AIConfig
    bus: BusConfig = field(default_factory=BusConfig)
    vetting: VettingConfig = field(default_factory=VettingConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)

    eod_api_key: str = field(default_factory=lambda: os.getenv("EOD_API_KEY", ""))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))

    def __post_init__(self) -> None:
        assert 0 < self.risk.max_position_size_pct <= 1, "max_position_size_pct must be in (0, 1]"
        assert 0 < self.risk.max_daily_loss_pct <= 1, "max_daily_loss_pct must be in (0, 1]"
        assert self.sentiment.sentiment_weight + self.sentiment.trend_weight == 1.0, "weights must sum to 1.0"
        assert self.signal.buy_threshold > 0, "buy_threshold must be positive"
        assert self.signal.sell_threshold < 0, "sell_threshold must be negative"


# ---------------------------------------------------------------------------
# Market Profiles
# ---------------------------------------------------------------------------

def get_india_config() -> Config:
    return Config(
        kite=KiteConfig(),
        alpaca=AlpacaConfig(),
        market=MarketConfig(
            exchange="NSE",
            currency="INR",
            timezone="Asia/Kolkata",
            calendar="NSE",
            open_hour=9,
            open_minute=15,
            close_hour=15,
            close_minute=30,
            pre_market_hour=9,
        ),
        universe=UniverseConfig(
            tickers=["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS", 
                     "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "AXISBANK.NS", 
                     "LT.NS", "KOTAKBANK.NS", "SUNPHARMA.NS", "M&M.NS", "ULTRACEMCO.NS", 
                     "HCLTECH.NS", "TITAN.NS", "ASIANPAINT.NS", "BAJFINANCE.NS", "MARUTI.NS",
                     "INDUSINDBK.NS", "BANKBARODA.NS", "PNB.NS", "FEDERALBNK.NS", 
                     "IDFCFIRSTB.NS", "AUBANK.NS", "BANDHANBNK.NS"]
        ),
        risk=RiskConfig(
            stop_loss_pct=0.025,            # -2.5% hard stop floor (ATR-dynamic widens further)
            profit_lock_threshold=0.005,    # +0.5% before profit-lock activates
            trailing_gap_base=0.010,        # 1.0% trailing gap (IN mid-caps are more volatile)
        ),
        wallet=WalletConfig(min_trade_value=1000.0),
        trend=TrendConfig(),
        sentiment=SentimentConfig(),
        signal=SignalConfig(),
        agent=AgentConfig(),
        ai=AIConfig(),
        strategy=StrategyConfig(index_symbol="^NSEI"),
    )


def get_us_config() -> Config:
    return Config(
        kite=KiteConfig(),
        alpaca=AlpacaConfig(),
        market=MarketConfig(
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
            calendar="XNYS",  # New York Stock Exchange calendar
            open_hour=9,
            open_minute=30,
            close_hour=16,
            close_minute=0,
            pre_market_hour=8,
        ),
        universe=UniverseConfig(
            tickers=["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", 
                     "AVGO", "COST", "PEP", "NFLX", "CSCO", "TMUS", "INTC", 
                     "CMCSA", "AMD", "QCOM", "ADBE", "TXN", "AMGN",
                     "INTU", "ISRG", "AMAT", "LRCX", "MU",
                     "PANW", "SNPS", "KLAC", "MELI", "CRWD"]
        ),
        risk=RiskConfig(),
        wallet=WalletConfig(
            min_trade_value=10.0,
            daily_spend_cap=float(os.getenv("DAILY_SPEND_CAP", "10000.0"))
        ),
        trend=TrendConfig(),
        sentiment=SentimentConfig(),
        signal=SignalConfig(),
        agent=AgentConfig(),
        ai=AIConfig(),
        strategy=StrategyConfig(index_symbol="SPY"),
    )


if ACTIVE_MARKET == "US":
    config: Config = get_us_config()
    CUR_SYM = "$"
else:
    config: Config = get_india_config()
    CUR_SYM = "₹"

