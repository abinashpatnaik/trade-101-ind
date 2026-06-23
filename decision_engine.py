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
from dataclasses import dataclass
from typing import Dict, Optional

from config import config
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
    ai_decision: Optional[str] = None
    ai_reason: Optional[str] = None


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

    def __init__(self) -> None:
        self._risk = config.risk
        self._sig = config.signal
        self._sent = config.sentiment
        # Cooldown: {symbol: timestamp} — prevents re-buying a stock too soon after a loss
        self._sell_cooldowns: Dict[str, float] = {}
        self._cooldown_seconds: int = int(os.getenv("SELL_COOLDOWN_MINUTES", "30")) * 60
        logger.debug("DecisionEngine initialised (cooldown=%ds).", self._cooldown_seconds)

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

    def _compute_quantity(
        self,
        portfolio_value: float,
        current_price: float,
        atr: float,
    ) -> int:
        """
        Adaptive position sizing that works for any account size (£50 to £1M+).

        For small accounts (< £500): uses all available funds for a single
        position (1 share minimum), skipping stocks that cost more than the
        entire account.

        For normal accounts: ATR-based sizing capped at max_position_size_pct
        of portfolio, also hard-capped by current_price so order cost never
        exceeds the allocation.

        Minimum quantity is always 1 share.
        """
        if current_price <= 0:
            return 1

        # Hard cap: never spend more than max_position_size_pct of portfolio
        # OR the full portfolio for very small accounts
        if portfolio_value < 500:
            # Small account: use up to 95% of available funds on one position
            max_notional = portfolio_value * 0.95
        else:
            max_notional = portfolio_value * self._risk.max_position_size_pct

        # Can't even afford 1 share — skip
        if current_price > max_notional:
            return 0

        # ATR-based sizing capped by price-based notional limit
        if atr > 0:
            atr_qty = math.floor(max_notional / (atr * 2))
        else:
            atr_qty = math.floor(max_notional / current_price)

        # Hard cap by notional: never exceed max_notional
        price_qty = math.floor(max_notional / current_price)
        qty = min(atr_qty, price_qty)

        return max(1, qty)

    def _has_capacity(self, open_positions: Dict) -> bool:
        """Return True if the portfolio can take on another position."""
        return len(open_positions) < self._risk.max_open_positions

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
    ) -> Decision:
        """
        Produce a trading decision for *symbol*.

        Parameters
        ----------
        symbol:
            Bare LSE ticker.
        trend_signal:
            ``TrendSignal`` dataclass from TrendEngine.analyse().
        sentiment_score:
            Float in [-1.0, 1.0] from SentimentEngine.get_sentiment().
        current_price:
            Latest traded price in GBP.
        portfolio:
            Dict with keys:
              - ``portfolio_value`` (float) — total NAV in GBP.
              - ``available_funds`` (float) — free cash in GBP.
              - ``open_positions`` (dict) — mapping {symbol: position_dict}.

        Returns
        -------
        Decision
        """
        portfolio_value: float = float(portfolio.get("portfolio_value", 0.0))
        available_funds: float = float(portfolio.get("available_funds", 0.0))
        open_positions: Dict = portfolio.get("open_positions", {})

        combined_score = self._compute_combined_score(
            trend_signal.overall_trend, sentiment_score
        )
        confidence = round(abs(combined_score), 4)

        logger.debug(
            "Decision input — %s: trend=%.4f sentiment=%.4f combined=%.4f "
            "buy_thr=%.2f sell_thr=%.2f",
            symbol,
            trend_signal.overall_trend,
            sentiment_score,
            combined_score,
            self._sig.buy_threshold,
            self._sig.sell_threshold,
        )

        already_held = symbol in open_positions

        # ---------------------------------------------------------------
        # Momentum reversal override (checked before BUY/SELL/HOLD logic)
        # ---------------------------------------------------------------
        if self.detect_reversal(symbol, trend_signal, portfolio, current_price):
            if already_held:
                position = open_positions[symbol]
                quantity = int(position.get("quantity", 0))
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
                        combined_score=combined_score,
                    )

        # ---------------------------------------------------------------
        # BUY logic
        # ---------------------------------------------------------------
        if combined_score >= self._sig.buy_threshold:
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
                    combined_score=combined_score,
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
                    combined_score=combined_score,
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
                    combined_score=combined_score,
                )

            # --- Max deployment check (e.g. only invest 50% of purse) ---
            max_deploy_pct = config.wallet.max_deploy_pct
            total_deployed = sum(
                float(p.get("market_value", 0))
                for p in open_positions.values()
            )
            max_deployable = portfolio_value * max_deploy_pct
            if total_deployed >= max_deployable:
                logger.info(
                    "Max deployment reached for %s — deployed £%.2f / £%.2f (%.0f%% of purse). "
                    "Holding until positions close and free up capital.",
                    symbol, total_deployed, max_deployable, max_deploy_pct * 100,
                )
                return Decision(
                    action="HOLD",
                    confidence=confidence,
                    reason=(
                        f"Max deployment {max_deploy_pct*100:.0f}% reached — "
                        f"£{total_deployed:.2f} deployed of £{max_deployable:.2f} allowed. "
                        "Waiting for positions to close before investing more."
                    ),
                    quantity=0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    combined_score=combined_score,
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
                    combined_score=combined_score,
                )

            quantity = self._compute_quantity(
                portfolio_value, current_price, trend_signal.atr
            )

            # Further cap quantity by remaining daily budget
            if remaining_budget < float("inf") and current_price > 0:
                budget_qty = max(1, int(remaining_budget / current_price))
                quantity = min(quantity, budget_qty)

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
                    combined_score=combined_score,
                )

            stop_loss_price = round(
                current_price * (1.0 - self._risk.stop_loss_pct), 4
            )
            take_profit_price = round(
                current_price * (1.0 + self._risk.take_profit_pct), 4
            )

            reason = (
                f"BUY signal — combined_score={combined_score:.3f} "
                f"(trend={trend_signal.overall_trend:.3f}, "
                f"sentiment={sentiment_score:.3f}). "
                f"RSI={trend_signal.rsi:.1f}, EMA={trend_signal.ema_signal}, "
                f"MACD={trend_signal.macd_signal}, VWAP={trend_signal.vwap_signal}. "
                f"SL={stop_loss_price:.4f}, TP={take_profit_price:.4f}."
            )

            logger.info(
                "BUY decision for %s: qty=%d price=%.4f sl=%.4f tp=%.4f",
                symbol, quantity, current_price, stop_loss_price, take_profit_price,
            )

            return Decision(
                action="BUY",
                confidence=confidence,
                reason=reason,
                quantity=quantity,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                combined_score=combined_score,
            )

        # ---------------------------------------------------------------
        # SELL logic
        # ---------------------------------------------------------------
        if combined_score <= self._sig.sell_threshold:
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
                    combined_score=combined_score,
                )

            position = open_positions[symbol]
            quantity = int(position.get("quantity", 0))
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
                    combined_score=combined_score,
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
                combined_score=combined_score,
            )

        # ---------------------------------------------------------------
        # HOLD — score within [-sell_threshold, buy_threshold)
        # ---------------------------------------------------------------
        return Decision(
            action="HOLD",
            confidence=confidence,
            reason=(
                f"No signal — combined_score={combined_score:.3f} is within "
                f"hold band [{self._sig.sell_threshold:.2f}, "
                f"{self._sig.buy_threshold:.2f}]."
            ),
            quantity=0,
            stop_loss_price=0.0,
            take_profit_price=0.0,
            combined_score=combined_score,
        )
