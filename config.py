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
    # Block NEW entries within this many minutes of the close. Late entries
    # rarely develop before the forced EOD flat-close and just bleed friction
    # (backtest: EOD exits are consistently net-negative). Existing positions
    # are still exit-managed inside this window — only new BUYs are paused.
    no_entry_buffer_minutes: int = 30


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
    # Profit-lock trailing stop: minimum CURRENT gain to activate.
    # Small-account tuning: locking gains below round-trip cost converts
    # gross wins into net losses, so the floor sits above typical friction.
    profit_lock_threshold: float = 0.0075   # +0.75% (US default, floor for ATR-dynamic)
    # Base trailing gap for small gains (widest gap in the graduated table)
    trailing_gap_base: float = 0.008        # 0.8% (US default)
    # US regulatory: max same-day round trips per rolling 5 business days for
    # sub-$25K margin accounts (Pattern Day Trader rule). Entries are blocked
    # when no day-trade slot is available so protective exits never get stuck.
    max_day_trades_per_5d: int = field(default_factory=lambda: int(os.getenv("MAX_DAY_TRADES_5D", "3")))
    # Whether a position the SWING model likes may be carried into delivery at
    # the EOD close-all. Measured on 50 live IN round trips (2026-07-07..07-21):
    # overnight holds averaged -Rs31.82/trade vs -Rs7.65 same-day, won 23% vs
    # 36%, and two gap exits alone were half the period's entire loss.
    # Defaults True so behaviour is unchanged unless a market opts out — US
    # MUST keep it, because flattening daily would burn the sub-$25K PDT
    # day-trade budget enforced by agents.pdt_guard.
    allow_overnight_hold: bool = field(
        default_factory=lambda: str(os.getenv("ALLOW_OVERNIGHT_HOLD", "true")).lower() == "true"
    )


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
    # Cost gate: expected move (2×ATR as % of price) must be at least this
    # multiple of the estimated round-trip cost, or the BUY is blocked.
    min_edge_multiple: float = field(default_factory=lambda: float(os.getenv("MIN_EDGE_MULTIPLE", "2.0")))
    # Overbought / blow-off guard: block BUYs when RSI >= this (0 = disabled).
    # Parabolic RSI spikes tend to spike-and-reverse (see the MBAPL loss).
    # Backtest: helps the IN traded universe, hurt US — so enabled IN-only.
    rsi_overbought_block: float = 0.0


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
    # Require at least this many backtest trades to APPROVE a symbol; below it,
    # block rather than pass on absence of evidence. 0 keeps the original
    # "zero trades is a neutral PASS" behavior (raise deliberately after
    # observing approval/block rates on the ML-aligned backtest).
    min_backtest_trades: int = 0
    # Hybrid per-stock buy threshold: during vetting, each nominated symbol's
    # buy bar is set to this percentile of its OWN backtest ML-confidence
    # distribution (bounded [0.50, 0.90]), requiring at least min_bars samples.
    # p80 = "trade the top ~20% most-confident signals for this stock."
    dynamic_threshold_pctile: float = 80.0   # US profile raises to 85.0
    dynamic_threshold_min_bars: int = 12
    # Live-accuracy blocklist
    accuracy_lookback_sessions: int = 5
    accuracy_window_trades: int = 10
    min_trades_to_judge: int = 3
    min_hit_rate: float = 0.40
    consecutive_stop_losses_to_block: int = 2
    # Liquidity screen: block symbols whose MEDIAN daily traded value over the
    # backtest lookback is below this (illiquid names = wide spreads that the
    # slippage model can't capture). Overridden per market profile.
    min_daily_turnover: float = 50_000_000.0


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
    # Restart the trader this many minutes before the open. Zerodha access
    # tokens expire each morning (~07:30 IST); a trader process that spans that
    # rollover loses its KiteTicker websocket (403) and can NEVER recover it
    # in-process, because kiteconnect runs on Twisted whose reactor is not
    # restartable. A fresh pre-open process re-authenticates and reconnects.
    # Must be < the token expiry→open gap so the restart lands after expiry.
    trader_restart_minutes_before_open: int = 20


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
            profit_lock_threshold=0.010,    # +1.0% before profit-lock activates (covers IN friction)
            trailing_gap_base=0.010,        # 1.0% trailing gap (IN mid-caps are more volatile)
            # Small-account concentration: 2 larger positions amortise the
            # fixed DP charge far better than 3 tiny ones.
            max_open_positions=2,
        ),
        wallet=WalletConfig(min_trade_value=3000.0),
        trend=TrendConfig(),
        sentiment=SentimentConfig(),
        # Hybrid per-stock thresholds (data-backed, validated out-of-sample):
        # ml_buy_threshold is now only the FALLBACK for symbols the vetting agent
        # couldn't calibrate; the live bar is each stock's own p80 confidence
        # percentile. A flat floor over-restricted (froze the dashboard). Keep
        # the RSI>=82 blow-off guard.
        signal=SignalConfig(ml_buy_threshold=0.58, rsi_overbought_block=82.0),
        agent=AgentConfig(),
        ai=AIConfig(),
        strategy=StrategyConfig(index_symbol="^NSEI"),
        vetting=VettingConfig(min_daily_turnover=50_000_000.0),   # ₹5 crore/day median
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
        # Hybrid per-stock thresholds (fallback only; live bar is each stock's
        # own p85 confidence percentile — see get_us_config's vetting profile).
        # No RSI guard for US (it hurt US in backtest).
        signal=SignalConfig(ml_buy_threshold=0.58),
        agent=AgentConfig(),
        ai=AIConfig(),
        strategy=StrategyConfig(index_symbol="SPY"),
        vetting=VettingConfig(
            min_daily_turnover=5_000_000.0,          # $5M/day median
            dynamic_threshold_pctile=85.0,           # p85 was best OOS on US targets
        ),
    )


if ACTIVE_MARKET == "US":
    config: Config = get_us_config()
    CUR_SYM = "$"
else:
    config: Config = get_india_config()
    CUR_SYM = "₹"

