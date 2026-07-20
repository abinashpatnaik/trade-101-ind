"""
agents.strategy
===============
STRATEGY agent: detects the market regime from the index (IN: ^NSEI,
US: SPY) and publishes a parameter directive the trader's DecisionEngine
overlays (see DecisionEngine.apply_directive).

Regimes and directives (seeded from live config values):

| Param                  | TRENDING | RANGING | VOLATILE |
|------------------------|----------|---------|----------|
| buy_threshold          | 0.45     | 0.55    | 0.60     |
| ml_buy_threshold_delta | -0.02    | +0.05   | +0.08    |
| sniper_min_adx         | 25       | 30      | 25       |
| trailing_gap_multiplier| 1.25     | 0.80    | 1.50     |
| position_size_multiplier| 1.00    | 0.75    | 0.50     |
| max_open_positions     | 3        | 2       | 2        |

Classification (VOLATILE evaluated first):
- VOLATILE: daily ATR% > threshold (2.5%) OR > 90th percentile of trailing 3mo
- TRENDING: intraday ADX(14) >= 25 with a meaningful EMA20 slope
- RANGING : otherwise

Hysteresis: two consecutive agreeing reads before switching regimes, so the
trader's parameters don't flap at classification boundaries. Absent/stale
directives revert the trader to exact config defaults — publishing nothing
is always safe.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from agents.base import BaseAgent

REGIME_DIRECTIVES: Dict[str, Dict[str, float]] = {
    "TRENDING": {
        "buy_threshold": 0.45,
        "ml_buy_threshold_delta": -0.02,
        "sniper_min_adx": 25,
        "trailing_gap_multiplier": 1.25,
        "position_size_multiplier": 1.00,
        "max_open_positions": 3,
    },
    "RANGING": {
        "buy_threshold": 0.55,
        "ml_buy_threshold_delta": 0.05,
        "sniper_min_adx": 30,
        "trailing_gap_multiplier": 0.80,
        "position_size_multiplier": 0.75,
        "max_open_positions": 2,
    },
    "VOLATILE": {
        "buy_threshold": 0.60,
        "ml_buy_threshold_delta": 0.08,
        "sniper_min_adx": 25,
        "trailing_gap_multiplier": 1.50,
        "position_size_multiplier": 0.50,
        "max_open_positions": 2,
    },
}

#: Minimum |EMA20 slope| (fraction of price per day) for a trend to count.
MIN_TREND_SLOPE = 0.0005


def classify_regime(
    adx: float,
    atr_pct: float,
    atr_pct_p90: float,
    ema20_slope: float,
    adx_trending: float = 25.0,
    atr_volatile: float = 0.025,
) -> str:
    """Pure regime classification from indicator values."""
    if atr_pct > atr_volatile or (atr_pct_p90 > 0 and atr_pct > atr_pct_p90):
        return "VOLATILE"
    if adx >= adx_trending and abs(ema20_slope) >= MIN_TREND_SLOPE:
        return "TRENDING"
    return "RANGING"


class Hysteresis:
    """Require N consecutive agreeing reads before switching regimes."""

    def __init__(self, reads_required: int = 2, initial: str = "RANGING") -> None:
        self.current = initial
        self._candidate: Optional[str] = None
        self._count = 0
        self._required = max(1, reads_required)

    def update(self, observed: str) -> str:
        if observed == self.current:
            self._candidate = None
            self._count = 0
            return self.current
        if observed == self._candidate:
            self._count += 1
        else:
            self._candidate = observed
            self._count = 1
        if self._count >= self._required:
            self.current = observed
            self._candidate = None
            self._count = 0
        return self.current


def compute_index_indicators(df_intraday: pd.DataFrame, df_daily: pd.DataFrame) -> Dict[str, float]:
    """ADX(14) from intraday bars; ATR%/p90 and EMA20 slope from daily bars.

    Pure pandas/numpy — no TrendEngine dependency so it stays testable with
    synthetic frames.
    """
    out = {"adx": 0.0, "atr_pct": 0.0, "atr_pct_p90": 0.0, "ema20_slope": 0.0}

    # --- ADX(14) on intraday bars (Wilder's smoothing approximated w/ EMA) ---
    if df_intraday is not None and len(df_intraday) > 30:
        high = df_intraday["High"].astype(float)
        low = df_intraday["Low"].astype(float)
        close = df_intraday["Close"].astype(float)
        up = high.diff()
        down = -low.diff()
        plus_dm = up.where((up > down) & (up > 0), 0.0)
        minus_dm = down.where((down > up) & (down > 0), 0.0)
        tr = pd.concat(
            [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.ewm(alpha=1 / 14, adjust=False).mean()
        if not adx.dropna().empty:
            out["adx"] = float(adx.dropna().iloc[-1])

    # --- Daily ATR%, its trailing p90, and EMA20 slope ---
    if df_daily is not None and len(df_daily) > 25:
        high = df_daily["High"].astype(float)
        low = df_daily["Low"].astype(float)
        close = df_daily["Close"].astype(float)
        tr = pd.concat(
            [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_pct_series = (atr / close).dropna()
        if not atr_pct_series.empty:
            out["atr_pct"] = float(atr_pct_series.iloc[-1])
            out["atr_pct_p90"] = float(np.percentile(atr_pct_series.values, 90))

        ema20 = close.ewm(span=20, adjust=False).mean()
        tail = ema20.tail(5)
        if len(tail) == 5 and tail.iloc[-1] > 0:
            # Least-squares slope over the last 5 days, normalised by price
            slope = np.polyfit(np.arange(5), tail.values, 1)[0]
            out["ema20_slope"] = float(slope / tail.iloc[-1])

    return out


class StrategyAgent(BaseAgent):
    name = "strategy"
    tick_seconds = 60.0

    def __init__(self) -> None:
        super().__init__()
        # Initialised here, NOT in setup(): BaseAgent.run() starts the command
        # listener BEFORE setup() runs, so an early cmd:classify (the
        # orchestrator sends one right after a restart) must not hit missing
        # attributes. _ready gates work that needs setup()'s state.
        self._classify_lock = threading.Lock()
        self._last_classify = 0.0
        self._ready = False

    def setup(self) -> None:
        from market_session import MarketSession
        from price_feed import PriceFeed

        self.session = MarketSession()
        self.price_feed = PriceFeed()  # yfinance only — never set_broker
        self.hysteresis = Hysteresis(self.config.strategy.hysteresis_reads)
        self._ready = True
        self.logger.info(
            "Strategy agent ready (index=%s).", self.config.strategy.index_symbol
        )

    # ------------------------------------------------------------------

    def _classify_and_publish(self) -> None:
        if not self._ready:
            self.logger.debug("Classify requested before setup completed — skipping.")
            return
        if not self._classify_lock.acquire(blocking=False):
            return
        try:
            self.bus.heartbeat(self.name, status="busy", detail="classify")
            index_symbol = self.config.strategy.index_symbol
            # Fetch the index via yfinance regardless of market: SPY / ^NSEI are
            # public and free, and this agent is broker-free (no Alpaca keys), so
            # the US→Alpaca route would fail with "keys not configured".
            df_intraday = self.price_feed.get_ohlcv(
                index_symbol, period="5d", interval="15m", force_yfinance=True)
            df_daily = self.price_feed.get_ohlcv(
                index_symbol, period="3mo", interval="1d", force_yfinance=True)
            if (df_intraday is None or df_intraday.empty) and (df_daily is None or df_daily.empty):
                self.logger.warning("No index data for %s — keeping previous directive.", index_symbol)
                return

            ind = compute_index_indicators(df_intraday, df_daily)
            observed = classify_regime(
                adx=ind["adx"],
                atr_pct=ind["atr_pct"],
                atr_pct_p90=ind["atr_pct_p90"],
                ema20_slope=ind["ema20_slope"],
                adx_trending=self.config.strategy.adx_trending_threshold,
                atr_volatile=self.config.strategy.atr_volatile_pct,
            )
            previous = self.hysteresis.current
            regime = self.hysteresis.update(observed)

            payload = {
                "regime": regime,
                "params": REGIME_DIRECTIVES[regime],
                "indicators": {
                    "adx": round(ind["adx"], 2),
                    "atr_pct": round(ind["atr_pct"], 5),
                    "atr_pct_p90": round(ind["atr_pct_p90"], 5),
                    "ema20_slope": round(ind["ema20_slope"], 6),
                    "observed": observed,
                },
            }
            self.bus.set_state("strategy", payload)
            if regime != previous:
                self.bus.publish("ev:strategy", payload)
                self.logger.info(
                    "REGIME CHANGE: %s -> %s (adx=%.1f atr_pct=%.2f%% slope=%.4f%%)",
                    previous, regime, ind["adx"], ind["atr_pct"] * 100,
                    ind["ema20_slope"] * 100,
                )
            else:
                self.logger.info(
                    "Regime: %s (observed=%s adx=%.1f atr_pct=%.2f%%)",
                    regime, observed, ind["adx"], ind["atr_pct"] * 100,
                )
        except Exception as exc:
            self.logger.error("Classification failed: %s", exc, exc_info=True)
        finally:
            self._classify_lock.release()

    # ------------------------------------------------------------------

    def on_command(self, payload: Dict[str, Any]) -> None:
        if payload.get("cmd") == "classify":
            threading.Thread(
                target=self._classify_and_publish, daemon=True, name="classify"
            ).start()

    def tick(self) -> None:
        # Self-timed classification while the market is open (orchestrator
        # also sends cmd:classify — the lock + interval guard make it cheap).
        import time

        if not self.session.is_market_open():
            return
        interval = self.config.strategy.classify_interval_minutes * 60
        if time.monotonic() - self._last_classify < interval:
            return
        self._last_classify = time.monotonic()
        self._classify_and_publish()


def main() -> None:
    StrategyAgent().run()


if __name__ == "__main__":
    main()
