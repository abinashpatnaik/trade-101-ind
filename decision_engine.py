"""
decision_engine.py
==================
Combines technical trend signals and news sentiment into concrete trading
decisions (BUY / SELL / HOLD) with position sizing, stop-loss, and
take-profit levels.

The combined signal formula is:
    combined_score = trend_weight * overall_trend + sentiment_weight * sentiment_score

Rules
-----
- BUY   if combined_score >= buy_threshold
         AND the symbol is not already in the portfolio
         AND portfolio has room for another open position
         AND available funds cover the intended order cost
- SELL  if combined_score <= sell_threshold
         AND the symbol IS currently held
- HOLD  in all other cases

Position sizing is ATR-based:
    quantity = floor((portfolio_value * max_position_size_pct) / (atr * 2))

The ATR multiplier of 2 creates a natural stop distance equal to 2 × ATR,
matching the configured stop_loss_pct risk per trade.
"""

from __future__ import annotations

import logging
import math
import os
import time
import json
from dataclasses import dataclass
from typing import Dict, Optional

from config import config, ACTIVE_MARKET
from trading_costs import round_trip_cost_pct
from trend_engine import TrendSignal

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    """
    Output produced by DecisionEngine.make_decision().

    Fields
    ------
    action:
        ``'BUY'`` | ``'SELL'`` | ``'HOLD'``
    confidence:
        Normalised absolute value of the combined score (0.0–1.0).
        Higher values indicate stronger conviction.
    reason:
        Human-readable explanation of why this decision was made.
    quantity:
        Number of shares to trade.  0 for HOLD decisions.
    stop_loss_price:
        Suggested stop-loss trigger price (BUY only; 0.0 otherwise).
    take_profit_price:
        Suggested take-profit limit price (BUY only; 0.0 otherwise).
    combined_score:
        Raw weighted score that drove the decision, in [-1.0, 1.0].
    """

    action: str                  # 'BUY' | 'SELL' | 'HOLD'
    confidence: float            # [0.0, 1.0]
    reason: str
    quantity: int                # shares to trade
    stop_loss_price: float       # 0.0 for HOLD/SELL-to-close
    take_profit_price: float     # 0.0 for HOLD/SELL-to-close
    combined_score: float        # [-1.0, 1.0]
    trailing_stop_pct: float = 0.0 # Dynamic ATR-based trailing stop pct
    ai_decision: Optional[str] = None
    ai_reason: Optional[str] = None
    ml_confidence: float = 0.0
    ml_confidence_swing: float = 0.0

class DecisionEngine:
    """
    Stateless decision engine that maps signals → trading actions.

    The engine never maintains portfolio state itself; the caller must pass
    current portfolio data on each call so that position limits and fund
    checks can be enforced.

    Usage
    -----
    >>> engine = DecisionEngine()
    >>> decision = engine.make_decision(
    ...     symbol='HSBA',
    ...     trend_signal=trend_signal,
    ...     sentiment_score=0.2,
    ...     current_price=620.5,
    ...     portfolio={
    ...         'portfolio_value': 100_000,
    ...         'available_funds': 50_000,
    ...         'open_positions': {'AZN': {...}},
    ...     },
    ... )
    """

    #: Directive keys the strategy agent may override — anything else is ignored.
    DIRECTIVE_KEYS = frozenset({
        "buy_threshold",
        "ml_buy_threshold_delta",
        "sniper_min_adx",
        "trailing_gap_multiplier",
        "position_size_multiplier",
        "max_open_positions",
    })

    def __init__(self) -> None:
        self._risk = config.risk
        self._sig = config.signal
        self._sent = config.sentiment
        # Cooldown: {symbol: timestamp} — prevents re-buying a stock too soon after a loss
        self._sell_cooldowns: Dict[str, float] = {}
        self._cooldown_seconds: int = int(os.getenv("SELL_COOLDOWN_MINUTES", "30")) * 60
        # Strategy-agent parameter overlay; empty dict = exact default behavior
        self._directive: Dict[str, float] = {}

        self.ml_thresholds: Dict[str, Dict[str, float]] = {"day": {}, "swing": {}}
        self._thresholds_mtime: Dict[str, float] = {"day": 0.0, "swing": 0.0}
        self._load_ml_thresholds()

        logger.debug("DecisionEngine initialised (cooldown=%ds).", self._cooldown_seconds)

    # ------------------------------------------------------------------
    # Strategy directive overlay
    # ------------------------------------------------------------------

    def apply_directive(self, params: Dict) -> None:
        """
        Overlay regime parameters published by the strategy agent.

        Unknown keys are dropped. An empty/missing directive leaves the
        engine in its exact default (config-driven) behavior.
        """
        cleaned = {k: v for k, v in (params or {}).items() if k in self.DIRECTIVE_KEYS}
        if cleaned != self._directive:
            logger.info("Strategy directive applied: %s", cleaned)
        self._directive = cleaned

    def clear_directive(self) -> None:
        if self._directive:
            logger.info("Strategy directive cleared — reverting to config defaults.")
        self._directive = {}

    def _buy_threshold(self) -> float:
        return float(self._directive.get("buy_threshold", self._sig.buy_threshold))

    def _sniper_min_adx(self) -> float:
        return float(self._directive.get("sniper_min_adx", 25.0))

    def _max_open_positions(self) -> int:
        return int(self._directive.get("max_open_positions", self._risk.max_open_positions))

    def _load_ml_thresholds(self) -> None:
        """Loads ML thresholds from disk, reloading if modified."""
        for mode in ["day", "swing"]:
            path = os.path.join(os.path.dirname(__file__), "data", f"ml_thresholds_{ACTIVE_MARKET}_{mode}.json")
            if os.path.exists(path):
                mtime = os.path.getmtime(path)
                if mtime > self._thresholds_mtime[mode]:
                    try:
                        with open(path, 'r') as f:
                            self.ml_thresholds[mode] = json.load(f)
                        self._thresholds_mtime[mode] = mtime
                        logger.info("Loaded ML thresholds for %s mode from %s", mode, path)
                    except Exception as e:
                        logger.error("Failed to load ML thresholds: %s", e)

    def get_ml_buy_threshold(self, symbol: str, is_swing: bool) -> float:
        """Returns the dynamic ML threshold for a symbol.

        Uses per-symbol threshold if available (training symbols).
        Falls back to _GLOBAL_ threshold (75th percentile of all training thresholds).
        Last resort: static config value.

        The config ``signal.ml_buy_threshold`` additionally acts as a FLOOR on
        the result: concentration tuning raises the buy bar so only
        high-conviction entries trade, even where the trained per-symbol /
        _GLOBAL_ threshold sits lower (set per-market: IN 0.70, US 0.65).
        """
        self._load_ml_thresholds()
        mode = "swing" if is_swing else "day"
        # Extract base symbol if it has an extension (for IN market)
        clean_sym = symbol.replace('.NS', '') if ACTIVE_MARKET == "IN" else symbol
        thresholds = self.ml_thresholds[mode]
        
        # 1. Per-symbol threshold (training symbols only)
        if clean_sym in thresholds:
            base = thresholds[clean_sym]
        # 2. Global dynamic threshold (covers sector-scanner picks)
        elif "_GLOBAL_" in thresholds:
            base = thresholds["_GLOBAL_"]
        # 3. Static fallback
        else:
            base = self._sig.ml_buy_threshold
        # Config threshold is a FLOOR over the dynamic/per-symbol value.
        base = max(base, self._sig.ml_buy_threshold)
        return base + float(self._directive.get("ml_buy_threshold_delta", 0.0))

    def register_cooldown(self, symbol: str, is_loss: bool) -> None:
        """
        Register a cooldown after a sell.
        30 mins for losses to prevent revenge trading.
        5 mins for profits to prevent immediate whipsaw rebuying on the same candle.
        """
        cooldown_duration = self._cooldown_seconds if is_loss else 300  # 5 mins
        self._sell_cooldowns[symbol] = time.time() + cooldown_duration
        logger.info("Registered %.1f min cooldown for %s (is_loss=%s).", cooldown_duration / 60, symbol, is_loss)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_combined_score(
        self,
        overall_trend: float,
        sentiment_score: float,
    ) -> float:
        """
        Compute the weighted linear combination of trend and sentiment.

        Both inputs are expected in [-1.0, 1.0].  The result is clamped to
        the same range to prevent out-of-band values from edge cases.
        """
        raw = (
            self._sent.trend_weight * overall_trend
            + self._sent.sentiment_weight * sentiment_score
        )
        return float(max(-1.0, min(1.0, raw)))

    def compute_classic_score(
        self,
        overall_trend: float,
        sentiment_score: float,
    ) -> float:
        """Public alias of the classic trend+sentiment score (used by the
        vetting agent's backtest simulator)."""
        return self._compute_combined_score(overall_trend, sentiment_score)

    def _compute_quantity(
        self,
        portfolio_value: float,
        current_price: float,
        atr: float,
    ) -> float:
        """
        Adaptive position sizing that works for any account size ($50 to $1M+).
        Returns fractional share quantities for Alpaca.

        For small accounts (< $100): uses all available funds for a single
        position, skipping stocks that cost more than the entire account.

        For normal accounts: ATR-based sizing capped at max_position_size_pct
        of portfolio, also hard-capped by current_price so order cost never
        exceeds the allocation.

        Minimum quantity is 0.01 shares (Alpaca fractional minimum).
        """
        if current_price <= 0:
            return 0.01

        max_notional = portfolio_value * self._risk.max_position_size_pct

        # Can't even afford $1 worth — skip
        if max_notional < 1.0:
            return 0

        # ATR-based sizing capped by price-based notional limit
        if atr > 0:
            atr_qty = max_notional / (atr * 2)
        else:
            atr_qty = max_notional / current_price

        # Hard cap by notional: never exceed max_notional
        price_qty = max_notional / current_price
        qty = min(atr_qty, price_qty)

        # Strategy-directive sizing (e.g. half-size in volatile regimes)
        qty *= float(self._directive.get("position_size_multiplier", 1.0))

        import config
        if config.ACTIVE_MARKET == "IN":
            import math
            qty = math.floor(qty)
            return float(qty)
        else:
            # Round to 4 decimal places (Alpaca supports up to 9 but 4 is practical)
            qty = round(qty, 4)
            return max(0.01, qty)

    def _has_capacity(self, open_positions: Dict) -> bool:
        """Return True if the portfolio can take on another position."""
        return len(open_positions) < self._max_open_positions()

    def _can_afford(
        self,
        quantity: int,
        current_price: float,
        available_funds: float,
    ) -> bool:
        """Return True if available cash covers the intended purchase."""
        estimated_cost = quantity * current_price * 1.001  # 0.1% slippage buffer
        return available_funds >= estimated_cost

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_reversal(
        self,
        symbol: str,
        trend_signal: "TrendSignal",
        portfolio: Dict,
        current_price: float,
    ) -> bool:
        """
        Detect a momentum reversal that warrants early profit-taking.

        Returns True (i.e. EXIT signal) if ALL of the following are true:
          1. The symbol IS currently held (in portfolio open_positions) and at a profit.
          2. RSI > 65 (approaching overbought — profit-taking zone).
          3. MACD signal is 'bearish' (momentum fading).
          4. EMA signal is 'bearish' OR VWAP signal is 'below' (price weakening).

        Parameters
        ----------
        symbol:
            Bare LSE ticker.
        trend_signal:
            ``TrendSignal`` dataclass from TrendEngine.analyse().
        portfolio:
            Dict with at minimum ``{'open_positions': dict}``.
        current_price:
            Latest traded price.

        Returns
        -------
        bool
        """
        open_positions: Dict = portfolio.get("open_positions", {})

        # Condition 1: Symbol must be held.
        if symbol not in open_positions:
            return False

        position = open_positions[symbol]
        avg_cost = float(position.get("avg_cost", current_price))
        if current_price < avg_cost:
            return False  # Do not take early profit if we are currently at a loss

        rsi: float = float(trend_signal.rsi)
        macd: str = str(trend_signal.macd_signal).lower()
        ema: str = str(trend_signal.ema_signal).lower()
        vwap: str = str(trend_signal.vwap_signal).lower()

        # Condition 2: RSI approaching overbought.
        if rsi <= 65.0:
            return False

        # Condition 3: MACD momentum fading.
        if macd != "bearish":
            return False

        # Condition 4: Price weakening on at least one dimension.
        if ema != "bearish" and vwap != "below":
            return False

        logger.info(
            "Momentum reversal detected for %s — RSI=%.1f, MACD=%s, EMA=%s",
            symbol, rsi, trend_signal.macd_signal, trend_signal.ema_signal,
        )
        return True

    def make_decision(
        self,
        symbol: str,
        trend_signal: TrendSignal,
        sentiment_score: float,
        current_price: float,
        portfolio: Dict,
        ml_confidence_day: float = 0.0,
        ml_confidence_swing: float = 0.0,
    ) -> Decision:
        """
        Produce a trading decision for *symbol*.
        """
        portfolio_value: float = float(portfolio.get("portfolio_value", 0.0))
        available_funds: float = float(portfolio.get("available_funds", 0.0))
        open_positions: Dict = portfolio.get("open_positions", {})
        already_held = symbol in open_positions

        # Always use the DAY model for intraday decisions (whether entering or holding).
        # The SWING model is only explicitly used by agent.py at 15:45 to determine overnight holds.
        active_ml_confidence = ml_confidence_day
        is_ai_driver = config.ai.primary_driver and config.ai.enabled and active_ml_confidence > 0.0

        if is_ai_driver:
            combined_score = active_ml_confidence  # Keep as 0.0 - 1.0 for UI clarity
            confidence = active_ml_confidence
            active_buy_threshold = self.get_ml_buy_threshold(symbol, is_swing=False)
            buy_condition = active_ml_confidence >= active_buy_threshold
            sell_condition = active_ml_confidence <= 0.40
            logger.debug(
                "AI DRIVER Active — %s: day_prob=%.4f mapped_score=%.4f ml_buy_thr=%.2f",
                symbol, ml_confidence_day, combined_score, active_buy_threshold
            )
        else:
            combined_score = self._compute_combined_score(
                trend_signal.overall_trend, sentiment_score
            )
            confidence = round(abs(combined_score), 4)
            buy_condition = combined_score >= self._buy_threshold()
            sell_condition = combined_score <= self._sig.sell_threshold
            logger.debug(
                "Decision input — %s: trend=%.4f sentiment=%.4f combined=%.4f "
                "buy_thr=%.2f sell_thr=%.2f",
                symbol,
                trend_signal.overall_trend,
                sentiment_score,
                combined_score,
                self._buy_threshold(),
                self._sig.sell_threshold,
            )

        already_held = symbol in open_positions

        # ---------------------------------------------------------------
        # Momentum reversal override (checked before BUY/SELL/HOLD logic)
        # ---------------------------------------------------------------
        if self.detect_reversal(symbol, trend_signal, portfolio, current_price):
            if already_held:
                position = open_positions[symbol]
                quantity = float(position.get("quantity", 0))
                if quantity > 0:
                    reversal_reason = (
                        "Momentum reversal: RSI overbought + MACD bearish — "
                        "locking in profit"
                    )
                    logger.info(
                        "Reversal SELL override for %s: qty=%d price=%.4f",
                        symbol, quantity, current_price,
                    )
                    return Decision(
                        action="SELL",
                        confidence=0.85,
                        reason=reversal_reason,
                        quantity=quantity,
                        stop_loss_price=0.0,
                        take_profit_price=0.0,
                        combined_score=combined_score, ml_confidence=active_ml_confidence,
                    )

        # ---------------------------------------------------------------
        # BUY logic
        # ---------------------------------------------------------------
        if buy_condition:
            # --- Sniper Mode: ADX trend strength filter ---
            # Only trade in clear trends (ADX > 25). Protects against
            # whipsaw losses in choppy/sideways markets.
            # Applies in EVERY mode: the ML driver picks candidates, but a
            # trade still needs trend + volume confirmation (AND, not OR).
            adx = getattr(trend_signal, 'adx', 0.0)
            min_adx = self._sniper_min_adx()
            if adx > 0 and adx < min_adx:
                logger.info(
                    "BUY blocked for %s — ADX=%.1f (weak trend, need >%.0f). "
                    "Protecting capital by avoiding choppy market.",
                    symbol, adx, min_adx,
                )
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"BUY signal (score={combined_score:.3f}) but ADX={adx:.1f} "
                        "indicates weak/choppy trend. Waiting for stronger trend."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score, ml_confidence=active_ml_confidence,
                )

            # --- Sniper Mode: Volume confirmation filter ---
            # Only trade when volume is above average (>1.5x). Low volume
            # moves often reverse — high volume confirms conviction.
            vol_ratio = getattr(trend_signal, 'volume_ratio', 1.0)
            if vol_ratio > 0 and vol_ratio < 1.5:
                logger.info(
                    "BUY blocked for %s — volume_ratio=%.2f (need >1.5x avg). "
                    "No market conviction behind this move.",
                    symbol, vol_ratio,
                )
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"BUY signal (score={combined_score:.3f}) but volume "
                        f"is only {vol_ratio:.2f}x average. Waiting for conviction."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score, ml_confidence=active_ml_confidence,
                )

            if already_held:
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"BUY signal (score={combined_score:.3f}) but {symbol} "
                        "is already held."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score, ml_confidence=active_ml_confidence,
                )

            # --- Cooldown check: don't re-buy a stock we just sold at a loss ---
            cooldown_until = self._sell_cooldowns.get(symbol, 0)
            if time.time() < cooldown_until:
                remaining_min = (cooldown_until - time.time()) / 60
                logger.info(
                    "BUY blocked for %s — cooldown active (%.0f min remaining)",
                    symbol, remaining_min,
                )
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"BUY signal (score={combined_score:.3f}) but {symbol} "
                        f"is on cooldown for {remaining_min:.0f} more minutes after a recent loss."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score, ml_confidence=active_ml_confidence,
                )

            if not self._has_capacity(open_positions):
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"BUY signal (score={combined_score:.3f}) for {symbol} "
                        f"but max open positions ({self._risk.max_open_positions}) "
                        "already reached."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score, ml_confidence=active_ml_confidence,
                )

            # --- Max deployment check (e.g. only invest 50% of purse) ---
            if ACTIVE_MARKET == "US" and portfolio_value < 500:
                max_deploy_pct = 0.95
            elif ACTIVE_MARKET == "IN" and portfolio_value < 50000:
                max_deploy_pct = 0.95
            else:
                max_deploy_pct = config.wallet.max_deploy_pct
            total_deployed = sum(
                float(p.get("market_value", 0))
                for p in open_positions.values()
            )
            max_deployable = portfolio_value * max_deploy_pct
            currency_sym = "₹" if ACTIVE_MARKET == "IN" else "$" if ACTIVE_MARKET == "US" else "£"
            if total_deployed >= max_deployable:
                logger.info(
                    "Max deployment reached for %s — deployed %s%.2f / %s%.2f (%.0f%% of purse). "
                    "Holding until positions close and free up capital.",
                    symbol, currency_sym, total_deployed, currency_sym, max_deployable, max_deploy_pct * 100,
                )
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"Max deployment {max_deploy_pct*100:.0f}% reached — "
                        f"{currency_sym}{total_deployed:.2f} deployed of {currency_sym}{max_deployable:.2f} allowed. "
                        "Waiting for positions to close before investing more."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score, ml_confidence=active_ml_confidence,
                )

            # --- Wallet / daily spend cap check ---
            remaining_budget = portfolio.get("remaining_budget", float("inf"))
            budget_exhausted = portfolio.get("budget_exhausted", False)
            if budget_exhausted:
                daily_spent = portfolio.get("daily_spent", 0)
                daily_cap = portfolio.get("daily_spend_cap", 0)
                reinvested = portfolio.get("daily_realised_profit", 0)
                logger.info(
                    "Wallet cap reached for %s — spent £%.2f / cap £%.2f "
                    "(reinvested £%.2f). Holding — will reinvest when profits come in.",
                    symbol, daily_spent, daily_cap, reinvested,
                )
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"Daily spend cap £{daily_cap:.0f} reached — "
                        f"spent £{daily_spent:.2f}, reinvested profits £{reinvested:.2f}. "
                        "Waiting to reinvest from profits."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score, ml_confidence=active_ml_confidence,
                )

            # --- Overbought / blow-off guard ---
            # Skip parabolic entries: an extreme RSI spike tends to spike and
            # reverse immediately (e.g. MBAPL bought at RSI 94, underwater from
            # the first tick). Configurable per market; 0 disables.
            rsi_block = self._sig.rsi_overbought_block
            if rsi_block and trend_signal.rsi >= rsi_block:
                logger.info(
                    "BUY blocked for %s — RSI %.0f >= overbought guard %.0f "
                    "(blow-off risk).",
                    symbol, trend_signal.rsi, rsi_block,
                )
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"BUY signal (score={combined_score:.3f}) but RSI "
                        f"{trend_signal.rsi:.0f} ≥ overbought guard "
                        f"{rsi_block:.0f} — skipping blow-off entry."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score, ml_confidence=active_ml_confidence,
                )

            quantity = self._compute_quantity(
                portfolio_value, current_price, trend_signal.atr
            )

            # Further cap quantity by remaining daily budget
            if remaining_budget < float("inf") and current_price > 0:
                budget_qty = max(1, int(remaining_budget / current_price))
                quantity = min(quantity, budget_qty)

            # --- Cost gate: the expected move must clear round-trip friction ---
            # Expected move proxy is 2×ATR (the same scale the stop uses); the
            # trade must offer at least min_edge_multiple × estimated cost, or
            # the position's edge is smaller than what the broker/exchange take.
            notional = quantity * current_price
            cost_pct = round_trip_cost_pct(notional, overnight=False)
            expected_move_pct = (trend_signal.atr * 2.0) / current_price if current_price > 0 else 0.0
            required_pct = self._sig.min_edge_multiple * cost_pct
            if notional > 0 and expected_move_pct < required_pct:
                logger.info(
                    "BUY blocked for %s — expected move %.2f%% < %.1fx round-trip "
                    "cost %.2f%% (notional=%.0f). Edge smaller than friction.",
                    symbol, expected_move_pct * 100, self._sig.min_edge_multiple,
                    cost_pct * 100, notional,
                )
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"BUY signal (score={combined_score:.3f}) but expected move "
                        f"{expected_move_pct*100:.2f}% is below {self._sig.min_edge_multiple:.0f}x "
                        f"round-trip cost {cost_pct*100:.2f}% — not worth the friction."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score, ml_confidence=active_ml_confidence,
                )

            if not self._can_afford(quantity, current_price, available_funds):
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"BUY signal (score={combined_score:.3f}) for {symbol} "
                        f"but insufficient funds (need ≈£{quantity * current_price:.2f}, "
                        f"have £{available_funds:.2f})."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score, ml_confidence=active_ml_confidence,
                )

            # Dynamic ATR-based stop loss — adapts to each stock's volatility
            # Uses 2× ATR as the stop distance, bounded between config floor and 5%
            atr_multiplier = 2.0
            atr_pct = (trend_signal.atr * atr_multiplier) / current_price if current_price > 0 else 0.025
            dynamic_stop_loss_pct = max(self._risk.stop_loss_pct, min(0.05, atr_pct))

            stop_loss_price = round(
                current_price * (1.0 - dynamic_stop_loss_pct), 4
            )
            take_profit_price = round(
                current_price * (1.0 + self._risk.take_profit_pct), 4
            )
            
            # Dynamic trailing stop pct (same ATR basis, bounded 1% to 4%),
            # scaled by the strategy directive (wider in trends, tighter in ranges)
            trailing_mult = float(self._directive.get("trailing_gap_multiplier", 1.0))
            dynamic_trailing_stop_pct = max(0.01, min(0.04, atr_pct)) * trailing_mult
            dynamic_trailing_stop_pct = max(0.005, min(0.06, dynamic_trailing_stop_pct))

            reason = (
                f"BUY signal — combined_score={combined_score:.3f} "
                f"(trend={trend_signal.overall_trend:.3f}, "
                f"sentiment={sentiment_score:.3f}). "
                f"ML={active_ml_confidence:.2f} "
                f"ATR-stop={dynamic_stop_loss_pct*100:.1f}% "
                f"Using {quantity} shares."
            )

            logger.info(
                "BUY decision for %s: qty=%d price=%.4f sl=%.4f (ATR-stop=%.1f%%) tp=%.4f",
                symbol, quantity, current_price, stop_loss_price, dynamic_stop_loss_pct*100, take_profit_price,
            )

            return Decision(
                action="BUY",
                confidence=confidence,
                reason=reason,
                quantity=quantity,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                trailing_stop_pct=dynamic_trailing_stop_pct,
                combined_score=combined_score, ml_confidence=active_ml_confidence,
            )

        # ---------------------------------------------------------------
        # SELL logic
        # ---------------------------------------------------------------
        if sell_condition:
            if not already_held:
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"SELL signal (score={combined_score:.3f}) but {symbol} "
                        "is not held."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score, ml_confidence=active_ml_confidence,
                )

            position = open_positions[symbol]
            quantity = float(position.get("quantity", 0))
            avg_cost = float(position.get("avg_cost", current_price))

            # (Removed stubborn hold rule to allow Early Loss Cutting if momentum flips)

            if quantity <= 0:
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"SELL signal for {symbol} but position quantity is "
                        f"{quantity} (nothing to sell)."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score, ml_confidence=active_ml_confidence,
                )

            reason = (
                f"SELL signal — combined_score={combined_score:.3f} "
                f"(trend={trend_signal.overall_trend:.3f}, "
                f"sentiment={sentiment_score:.3f}). "
                f"RSI={trend_signal.rsi:.1f}, EMA={trend_signal.ema_signal}, "
                f"MACD={trend_signal.macd_signal}."
            )

            logger.info(
                "SELL decision for %s: qty=%d price=%.4f",
                symbol, quantity, current_price,
            )

            # Record cooldown if selling at a loss
            if current_price < avg_cost:
                self._sell_cooldowns[symbol] = time.time() + self._cooldown_seconds
                logger.info(
                    "Cooldown activated for %s: blocked from re-buying for %d minutes",
                    symbol, self._cooldown_seconds // 60,
                )

            return Decision(
                action="SELL",
                confidence=confidence,
                reason=reason,
                quantity=quantity,
                stop_loss_price=0.0,
                take_profit_price=0.0,
                combined_score=combined_score, ml_confidence=active_ml_confidence,
            )

        # ---------------------------------------------------------------
        # HOLD — score within [-sell_threshold, buy_threshold)
        # ---------------------------------------------------------------
        if is_ai_driver:
            reason_str = (
                f"No signal — ML_confidence={combined_score:.3f} is within "
                f"hold band [0.40, {active_buy_threshold:.2f}]."
            )
        else:
            reason_str = (
                f"No signal — combined_score={combined_score:.3f} is within "
                f"hold band [{self._sig.sell_threshold:.2f}, "
                f"{self._buy_threshold():.2f}]."
            )

        return Decision(
            action="HOLD",
            confidence=confidence,
            reason=reason_str,
            quantity=0,
            stop_loss_price=0.0,
            take_profit_price=0.0,
            combined_score=combined_score, ml_confidence=active_ml_confidence,
        )
