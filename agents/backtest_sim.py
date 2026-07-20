"""
agents.backtest_sim
===================
Pure OHLCV replay engine used by the vetting agent's backtest screen.

Replays historical bars through the SAME entry logic as the live system
(``DecisionEngine.make_decision`` classic path — trend + sniper gates) and a
pure reimplementation of ``OrderExecutor.check_exit_conditions``'s exit math
(hard stop / take profit / ATR profit-lock graduated trailing stop).

Deliberately broker-free and deterministic:
- sentiment is fixed at 0.0
- entries/exits are at bar close
- one position at a time, flat at each session close (day-trade screening)

Do NOT import order_executor here — it drags broker dependencies. The exit
math below mirrors order_executor.py::check_exit_conditions and must be kept
in sync with it (unit tests in tests/test_backtest_sim.py pin the behavior).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from config import config
from trading_costs import round_trip_cost_pct as _rt_cost

logger = logging.getLogger(__name__)


def _default_cost_pct() -> float:
    """Representative round-trip cost for a typical small-account position
    (2× the market's min trade value, same-day)."""
    return _rt_cost(config.wallet.min_trade_value * 2, overnight=False)


@dataclass
class SimParams:
    """Exit/entry parameters (defaults mirror the live config)."""
    stop_loss_pct: float = field(default_factory=lambda: config.risk.stop_loss_pct)
    take_profit_pct: float = field(default_factory=lambda: config.risk.take_profit_pct)
    profit_lock_threshold: float = field(default_factory=lambda: config.risk.profit_lock_threshold)
    trailing_gap_base: float = field(default_factory=lambda: config.risk.trailing_gap_base)
    # Deducted from every simulated trade so verdicts are NET of friction;
    # also lifts the profit-lock floor to entry×(1+cost) like the live executor.
    round_trip_cost_pct: float = field(default_factory=_default_cost_pct)
    warmup_bars: int = 50
    max_window_bars: int = 200


@dataclass
class SimTrade:
    entry_ts: str
    exit_ts: str
    entry_price: float
    exit_price: float
    return_pct: float
    exit_reason: str


@dataclass
class SimResult:
    symbol: str
    n_trades: int = 0
    wins: int = 0
    total_return_pct: float = 0.0
    avg_return_pct: float = 0.0
    trades: List[SimTrade] = field(default_factory=list)
    error: Optional[str] = None
    # Day-model ML confidence at every entry-eligible bar (populated only when
    # an ai_validator is supplied). Used to calibrate a per-stock buy threshold.
    ml_day_values: List[float] = field(default_factory=list)


@dataclass
class _Position:
    entry_ts: str
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    initial_trailing_pct: float
    high_water: float


def simulate_exit(
    pos: _Position,
    current_price: float,
    params: SimParams,
) -> Optional[str]:
    """
    One bar of exit checking. Mirrors OrderExecutor.check_exit_conditions.

    Mutates ``pos.high_water`` exactly like the live tracker (advance on new
    highs; reset to current price while the profit-lock is inactive).
    Returns 'STOP_LOSS' | 'TAKE_PROFIT' | 'TRAILING_STOP' | None.
    """
    if current_price <= 0:
        return None

    # Advance the high-water mark
    if current_price > pos.high_water:
        pos.high_water = current_price

    gain_from_entry = (current_price / pos.entry_price) - 1.0 if pos.entry_price > 0 else 0.0
    gain_from_high = (pos.high_water / pos.entry_price) - 1.0 if pos.entry_price > 0 else 0.0

    # --- 1. Hard stop loss ---
    if pos.stop_loss_price > 0 and current_price <= pos.stop_loss_price:
        return "STOP_LOSS"

    # --- 2. Take profit ---
    if pos.take_profit_price > 0 and current_price >= pos.take_profit_price:
        return "TAKE_PROFIT"

    # --- 3. ATR-based profit-lock graduated trailing stop ---
    atr_gap = pos.initial_trailing_pct if pos.initial_trailing_pct > 0 else 0.0
    config_gap = params.trailing_gap_base
    base_gap = max(atr_gap, config_gap) if atr_gap > 0 else config_gap

    if gain_from_entry >= params.profit_lock_threshold:
        if gain_from_high >= 0.03:
            trail_gap = base_gap * 0.33
        elif gain_from_high >= 0.02:
            trail_gap = base_gap * 0.50
        elif gain_from_high >= 0.01:
            trail_gap = base_gap * 0.67
        elif gain_from_high >= 0.005:
            trail_gap = base_gap * 0.83
        else:
            trail_gap = base_gap

        trigger = pos.high_water * (1.0 - trail_gap)
        # Never let the trailing stop go below NET break-even (entry + costs),
        # mirroring the live executor's cost-aware floor.
        trigger = max(trigger, pos.entry_price * (1.0 + params.round_trip_cost_pct))

        if current_price <= trigger:
            return "TRAILING_STOP" if current_price >= pos.entry_price else "STOP_LOSS"
    else:
        # Profit-lock inactive — reset high to the current price so a later
        # recovery trails from the recovery point, not a stale high.
        pos.high_water = current_price

    return None


def _entry_levels(price: float, atr: float, params: SimParams) -> Tuple[float, float, float]:
    """Stop/target/trailing levels exactly as DecisionEngine computes on BUY."""
    atr_pct = (atr * 2.0) / price if price > 0 else 0.025
    dynamic_stop_pct = max(params.stop_loss_pct, min(0.05, atr_pct))
    stop_loss_price = round(price * (1.0 - dynamic_stop_pct), 4)
    take_profit_price = round(price * (1.0 + params.take_profit_pct), 4)
    initial_trailing_pct = max(0.01, min(0.04, atr_pct))
    return stop_loss_price, take_profit_price, initial_trailing_pct


def replay(
    symbol: str,
    df: pd.DataFrame,
    decision_engine,
    trend_engine,
    params: Optional[SimParams] = None,
    ai_validator=None,
) -> SimResult:
    """
    Replay OHLCV bars through entry logic + exit math.

    ``df`` must have OHLCV columns (Open/High/Low/Close/Volume) and a
    DatetimeIndex (5m bars expected). ``decision_engine`` /``trend_engine``
    are live instances — the decision engine's current strategy directive
    (if any) is honored automatically.

    ``ai_validator``: when supplied (and its models are loaded), each bar's
    entry decision is scored with the SAME ML model the live system uses, so
    the screen tests the live ML-driven path instead of the classic trend path.
    When None (or models unavailable) ML confidence is 0.0 → classic path,
    preserving the original deterministic behavior.
    """
    params = params or SimParams()
    result = SimResult(symbol=symbol)

    if df is None or df.empty or len(df) <= params.warmup_bars:
        result.error = "insufficient data"
        return result

    # Synthetic portfolio: entries only need capacity/afford checks to pass.
    sim_portfolio = {
        "portfolio_value": 100_000.0,
        "available_funds": 100_000.0,
        "open_positions": {},
    }

    position: Optional[_Position] = None
    dates = df.index.date

    def _close_position(pos: _Position, ts, price: float, reason: str) -> None:
        ret = ((price / pos.entry_price) - 1.0) * 100.0 if pos.entry_price > 0 else 0.0
        # Net of estimated round-trip friction — a gross win smaller than
        # costs is a loss and must count as one.
        ret -= params.round_trip_cost_pct * 100.0
        result.trades.append(
            SimTrade(
                entry_ts=str(pos.entry_ts),
                exit_ts=str(ts),
                entry_price=pos.entry_price,
                exit_price=price,
                return_pct=ret,
                exit_reason=reason,
            )
        )
        result.n_trades += 1
        if ret > 0:
            result.wins += 1
        result.total_return_pct += ret

    for i in range(params.warmup_bars, len(df)):
        ts = df.index[i]
        price = float(df["Close"].iloc[i])
        if price <= 0:
            continue

        is_last_bar_of_session = (i == len(df) - 1) or (dates[i + 1] != dates[i])

        if position is not None:
            reason = simulate_exit(position, price, params)
            if reason is None and is_last_bar_of_session:
                reason = "EOD"
            if reason is not None:
                _close_position(position, ts, price, reason)
                position = None
            continue

        # No position — evaluate entry (never enter on the session's last bar)
        if is_last_bar_of_session:
            continue

        window_start = max(0, i - params.max_window_bars)
        window = df.iloc[window_start: i + 1]
        try:
            signal = trend_engine.analyse(symbol, window)
        except Exception as exc:
            logger.debug("replay(%s): analyse failed at %s: %s", symbol, ts, exc)
            continue
        if signal is None:
            continue

        # Score the bar with the live ML model when available, so the screen
        # matches the live AI-driven entry path. Sentiment stays 0.0 (no
        # point-in-time historical news in a replay). Falls back to 0.0 (classic
        # path) when no validator/model is present.
        if ai_validator is not None:
            ml_day = ai_validator.get_ml_confidence(signal, 0.0, "day")
            ml_swing = ai_validator.get_ml_confidence(signal, 0.0, "swing")
            result.ml_day_values.append(ml_day)
        else:
            ml_day = ml_swing = 0.0

        decision = decision_engine.make_decision(
            symbol=symbol,
            trend_signal=signal,
            sentiment_score=0.0,      # deterministic — no live news in a replay
            current_price=price,
            portfolio=sim_portfolio,
            ml_confidence_day=ml_day,
            ml_confidence_swing=ml_swing,
        )
        if decision.action != "BUY":
            continue

        stop, target, trail = _entry_levels(price, float(signal.atr), params)
        position = _Position(
            entry_ts=str(ts),
            entry_price=price,
            stop_loss_price=stop,
            take_profit_price=target,
            initial_trailing_pct=trail,
            high_water=price,
        )

    # Safety: close anything left open at the final bar
    if position is not None:
        _close_position(position, df.index[-1], float(df["Close"].iloc[-1]), "EOD")
        position = None

    if result.n_trades:
        result.avg_return_pct = result.total_return_pct / result.n_trades
    return result


def verdict(result: SimResult, ev_threshold_pct: float = 0.0, min_trades: int = 0) -> str:
    """
    'FAIL' (block the symbol) when the replay demonstrably fails the screen.

    - Data errors are always neutral PASSes (never block on missing data).
    - A symbol that traded is blocked when its net EV is below the threshold.
    - When ``min_trades`` > 0, a symbol that produced FEWER than ``min_trades``
      backtest trades is also blocked: with too little evidence we decline to
      approve rather than passing on absence of evidence. ``min_trades`` = 0
      preserves the original "zero trades is a neutral PASS" behavior.
    """
    if result.error is not None:
        return "PASS"
    if result.n_trades >= 1 and result.total_return_pct < ev_threshold_pct:
        return "FAIL"
    if min_trades > 0 and result.n_trades < min_trades:
        return "FAIL"
    return "PASS"
