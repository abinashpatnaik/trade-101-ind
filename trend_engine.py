"""
trend_engine.py
===============
Technical-analysis engine that computes a composite trend signal from
RSI, EMA crossover, MACD histogram, ATR, and VWAP.

Returns a TrendSignal dataclass with an overall_trend score in [-1.0, 1.0]
that is consumed by the DecisionEngine.

Requires:
    pip install pandas-ta>=0.3.14b pandas>=2.0.0 numpy>=1.26.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta_classic as ta

from config import config
from price_feed import PriceFeed

logger = logging.getLogger(__name__)


@dataclass
class TrendSignal:
    """
    Result produced by TrendEngine.analyse().

    Fields
    ------
    symbol:
        The ticker that was analysed.
    rsi:
        Current RSI value (0–100).
    ema_signal:
        ``'bullish'`` if EMA-short > EMA-long, ``'bearish'`` if EMA-short < EMA-long,
        else ``'neutral'``.
    macd_signal:
        ``'bullish'`` if MACD histogram is positive (or turning up),
        ``'bearish'`` if negative (or turning down), else ``'neutral'``.
    atr:
        Latest ATR value in price units — used for position sizing.
    vwap_signal:
        ``'above'`` if current price > VWAP, else ``'below'``.
    overall_trend:
        Weighted composite score in [-1.0, 1.0].
        Positive → bullish bias; negative → bearish bias.
    current_price:
        Last close price used for VWAP comparison.
    """

    symbol: str
    rsi: float
    ema_signal: str          # 'bullish' | 'bearish' | 'neutral'
    macd_signal: str         # 'bullish' | 'bearish' | 'neutral'
    atr: float
    vwap_signal: str         # 'above' | 'below'
    overall_trend: float     # [-1.0, 1.0]
    current_price: float
    adx: float = 0.0         # Average Directional Index (0-100)
    volume_ratio: float = 1.0  # Current volume / 20-day avg volume


class TrendEngine:
    """
    Computes technical trend signals for a single equity.

    Design
    ------
    Sub-signals are each normalised to a value in {-1.0, 0.0, +1.0} and
    then combined with equal weights into the overall_trend score.

    Sub-signals and their weights:
        - RSI-based signal      (weight 1 / 4)
        - EMA-crossover signal  (weight 1 / 4)
        - MACD-histogram signal (weight 1 / 4)
        - VWAP-position signal  (weight 1 / 4)

    ATR is computed but not included in the composite score; it is used
    by the DecisionEngine for ATR-based position sizing.

    Usage
    -----
    >>> engine = TrendEngine()
    >>> price_feed = PriceFeed()
    >>> df = price_feed.get_ohlcv('HSBA', period='5d', interval='5m')
    >>> signal = engine.analyse('HSBA', df)
    >>> print(signal.overall_trend)
    """

    # Sub-signal numeric values used in the weighted average.
    _BULLISH_VALUE = 1.0
    _BEARISH_VALUE = -1.0
    _NEUTRAL_VALUE = 0.0

    # Equal weights for the four sub-signals.
    _WEIGHTS = {
        "rsi":  0.25,
        "ema":  0.25,
        "macd": 0.25,
        "vwap": 0.25,
    }

    def __init__(self) -> None:
        self._cfg = config.trend
        logger.debug("TrendEngine initialised.")

    # ------------------------------------------------------------------
    # Internal helpers — individual sub-signals
    # ------------------------------------------------------------------

    def _compute_rsi_signal(self, close: pd.Series) -> tuple[float, float]:
        """
        Compute RSI and return (rsi_value, signal_score).

        signal_score:
          +1.0 if RSI < oversold threshold (oversold → potential long entry)
          -1.0 if RSI > overbought threshold (overbought → potential short/exit)
           0.0 otherwise
        """
        rsi_series = ta.rsi(close, length=self._cfg.rsi_period)
        if rsi_series is None or rsi_series.dropna().empty:
            return 50.0, self._NEUTRAL_VALUE

        rsi_val = float(rsi_series.iloc[-1])

        if rsi_val < self._cfg.rsi_oversold:
            return rsi_val, self._BULLISH_VALUE
        elif rsi_val > self._cfg.rsi_overbought:
            return rsi_val, self._BEARISH_VALUE
        else:
            # Scale the RSI to a linear signal in [-1, 1] within the neutral zone
            # so that scores trend progressively rather than snapping.
            mid = 50.0
            half_range = (self._cfg.rsi_overbought - self._cfg.rsi_oversold) / 2.0
            scaled = (rsi_val - mid) / half_range  # -1 → +1
            return rsi_val, float(np.clip(-scaled, -1.0, 1.0))  # negative → bullish

    def _compute_ema_signal(
        self, close: pd.Series
    ) -> tuple[str, float]:
        """
        Compute EMA crossover and return (label, signal_score).
        """
        ema_short = ta.ema(close, length=self._cfg.ema_short)
        ema_long = ta.ema(close, length=self._cfg.ema_long)

        if ema_short is None or ema_long is None:
            return "neutral", self._NEUTRAL_VALUE

        short_vals = ema_short.dropna()
        long_vals = ema_long.dropna()

        if short_vals.empty or long_vals.empty:
            return "neutral", self._NEUTRAL_VALUE

        s_val = float(short_vals.iloc[-1])
        l_val = float(long_vals.iloc[-1])

        if s_val > l_val:
            return "bullish", self._BULLISH_VALUE
        elif s_val < l_val:
            return "bearish", self._BEARISH_VALUE
        else:
            return "neutral", self._NEUTRAL_VALUE

    def _compute_macd_signal(
        self, close: pd.Series
    ) -> tuple[str, float]:
        """
        Compute MACD histogram and return (label, signal_score).

        Bullish condition: histogram is positive AND increased vs prior bar
        (momentum is strengthening upward).
        """
        macd_df = ta.macd(
            close,
            fast=self._cfg.macd_fast,
            slow=self._cfg.macd_slow,
            signal=self._cfg.macd_signal,
        )
        if macd_df is None or macd_df.empty:
            return "neutral", self._NEUTRAL_VALUE

        # pandas_ta names the histogram column MACDh_<fast>_<slow>_<signal>.
        hist_col = [c for c in macd_df.columns if c.startswith("MACDh_")]
        if not hist_col:
            return "neutral", self._NEUTRAL_VALUE

        hist = macd_df[hist_col[0]].dropna()
        if len(hist) < 2:
            return "neutral", self._NEUTRAL_VALUE

        current_hist = float(hist.iloc[-1])
        prev_hist = float(hist.iloc[-2])

        if current_hist > 0 and current_hist > prev_hist:
            return "bullish", self._BULLISH_VALUE
        elif current_hist < 0 and current_hist < prev_hist:
            return "bearish", self._BEARISH_VALUE
        elif current_hist > 0:
            # Positive but weakening.
            return "bullish", 0.5
        elif current_hist < 0:
            # Negative but recovering.
            return "bearish", -0.5
        else:
            return "neutral", self._NEUTRAL_VALUE

    def _compute_atr(self, high: pd.Series, low: pd.Series, close: pd.Series) -> float:
        """Return the most recent ATR value, or 0.0 on failure."""
        atr_series = ta.atr(high, low, close, length=self._cfg.atr_period)
        if atr_series is None or atr_series.dropna().empty:
            return 0.0
        return float(atr_series.dropna().iloc[-1])

    def _compute_adx(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
        """Compute the Average Directional Index (ADX).
        
        ADX measures trend strength regardless of direction.
        Values > 25 indicate a strong trend worth trading.
        """
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr_smooth = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr_smooth)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr_smooth)

        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1))
        adx = dx.rolling(window=period).mean()

        latest = adx.iloc[-1]
        return float(latest) if not np.isnan(latest) else 0.0

    def _compute_volume_ratio(self, volume: pd.Series, lookback: int = 20) -> float:
        """Compute current volume relative to the 20-period average.
        
        Assumes the input series already excludes the currently forming candle.
        A ratio > 1.5 indicates above-average volume (conviction).
        """
        if volume.empty or len(volume) < lookback + 1:
            return 1.0
        
        avg_vol = volume.iloc[-lookback:].mean()
        if avg_vol <= 0:
            return 1.0
        return float(volume.iloc[-1] / avg_vol)

    def _compute_vwap_signal(
        self, df: pd.DataFrame, current_price: float
    ) -> tuple[str, float]:
        """
        Compare current price to VWAP and return (label, signal_score).
        """
        from price_feed import PriceFeed

        vwap = PriceFeed.calculate_vwap(df)
        if vwap is None or vwap.dropna().empty:
            return "neutral", self._NEUTRAL_VALUE

        vwap_val = float(vwap.dropna().iloc[-1])

        if current_price > vwap_val:
            return "above", self._BULLISH_VALUE
        elif current_price < vwap_val:
            return "below", self._BEARISH_VALUE
        else:
            return "neutral", self._NEUTRAL_VALUE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyse(
        self,
        symbol: str,
        df: pd.DataFrame,
    ) -> Optional[TrendSignal]:
        """
        Run the full suite of technical indicators on *df* and return a
        ``TrendSignal``.

        Parameters
        ----------
        symbol:
            The ticker being analysed (for logging only).
        df:
            OHLCV DataFrame with columns Open, High, Low, Close, Volume.
            Must contain at least ``max(ema_long, macd_slow + macd_signal) + 5``
            rows to allow all indicators to compute non-NaN values.

        Returns
        -------
        TrendSignal or None
            Returns None if the DataFrame is invalid or too short for any
            indicator to produce a result.
        """
        if df is None or df.empty:
            logger.warning("TrendEngine.analyse(): empty DataFrame for %s", symbol)
            return None

        required_cols = {"Open", "High", "Low", "Close", "Volume"}
        if not required_cols.issubset(df.columns):
            logger.warning(
                "TrendEngine.analyse(): missing columns for %s. Have: %s",
                symbol, list(df.columns),
            )
            return None

        # Minimum bar count: enough for the longest indicator + buffer.
        min_bars = max(
            self._cfg.ema_long,
            self._cfg.macd_slow + self._cfg.macd_signal,
            self._cfg.rsi_period,
            self._cfg.atr_period,
        ) + 5

        if len(df) < min_bars:
            logger.warning(
                "TrendEngine.analyse(): insufficient bars for %s "
                "(have %d, need %d).",
                symbol, len(df), min_bars,
            )
            return None

        try:
            # Drop the currently forming (incomplete) candle to prevent 
            # mid-minute repainting jitter which causes erratic ML decisions.
            if len(df) > 1:
                df = df.iloc[:-1].copy()

            close = df["Close"].astype(float)
            high = df["High"].astype(float)
            low = df["Low"].astype(float)
            current_price = float(close.iloc[-1])

            # --- Compute each sub-signal ---
            adx_val = self._compute_adx(high, low, close)
            vol_ratio = self._compute_volume_ratio(df["Volume"].astype(float))
            rsi_val, rsi_score = self._compute_rsi_signal(close)
            ema_label, ema_score = self._compute_ema_signal(close)
            macd_label, macd_score = self._compute_macd_signal(close)
            atr_val = self._compute_atr(high, low, close)
            vwap_label, vwap_score = self._compute_vwap_signal(df, current_price)

            # --- Dynamic Regime Detection ---
            w_rsi = self._WEIGHTS["rsi"]
            w_ema = self._WEIGHTS["ema"]
            w_macd = self._WEIGHTS["macd"]
            w_vwap = self._WEIGHTS["vwap"]

            # If market is strongly trending, RSI overbought is normal momentum
            if adx_val >= 25.0:
                if rsi_score < 0:  # Suppress RSI overbought bearish penalty
                    rsi_score = 0.0
                    logger.debug("Strong trend (ADX=%.1f) detected for %s, suppressing RSI overbought penalty.", adx_val, symbol)
                # Shift weight from RSI to EMA/MACD momentum
                w_rsi = 0.0
                w_ema += 0.125
                w_macd += 0.125

            # --- Weighted composite score ---
            overall_trend = (
                w_rsi  * rsi_score
                + w_ema  * ema_score
                + w_macd * macd_score
                + w_vwap * vwap_score
            )
            # Clip to guarantee [-1, 1] even with floating-point imprecision.
            overall_trend = float(np.clip(overall_trend, -1.0, 1.0))

            signal = TrendSignal(
                symbol=symbol,
                rsi=round(rsi_val, 2),
                ema_signal=ema_label,
                macd_signal=macd_label,
                atr=round(atr_val, 4),
                vwap_signal=vwap_label,
                overall_trend=round(overall_trend, 4),
                current_price=round(current_price, 4),
                adx=round(adx_val, 2),
                volume_ratio=round(vol_ratio, 2),
            )

            logger.info(
                "TrendSignal %s: rsi=%.1f ema=%s macd=%s vwap=%s "
                "atr=%.4f adx=%.1f vol_ratio=%.2f overall=%.4f",
                symbol, signal.rsi, signal.ema_signal, signal.macd_signal,
                signal.vwap_signal, signal.atr, signal.adx, signal.volume_ratio,
                signal.overall_trend,
            )
            return signal

        except Exception as exc:
            logger.error(
                "TrendEngine.analyse() failed for %s: %s",
                symbol, exc, exc_info=True,
            )
            return None
