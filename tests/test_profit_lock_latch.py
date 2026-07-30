"""
Profit-lock trail is a one-way LATCH, not a per-tick gate.

THE BUG THIS PINS (observed live on GRMN, 2026-07-30)
-----------------------------------------------------
entry 288.80, ATR gap 1.3%, high-water 294.29 (+1.90% from entry).
Tier gap = 1.3% x 0.67 = 0.871%  ->  trailing trigger 291.73.

But the lock was re-tested every tick against profit_lock_threshold (US
+0.75%), so it only stayed armed while price >= 288.80 x 1.0075 = 290.97.
The sell could therefore only fire on a tick landing inside
[290.97, 291.73] — a window 0.26% of price wide. Price fell 293.42 ->
290.83, skipped the window, and the old `else` branch then did:

    self._trailing_high[symbol] = current_price   # 294.29 -> 290.83

which disarmed the trail permanently AND erased the peak. A locked +0.87%
turned into exposure to the -1.5% hard stop, unrecoverably: any later
recovery re-trailed from 290.83.

Worse, the window was NEGATIVE for gains under ~+1.7% from high — there the
trailing stop could never fire at all — and the "never below net break-even"
floor (289.44) sat below the disarm price (290.97), making it dead code.

These tests assert the trail arms once and stays armed, and that the
high-water mark only ever moves up.
"""

import pytest

from agents.backtest_sim import SimParams, _Position, simulate_exit

# --- the live GRMN position -------------------------------------------------
ENTRY = 288.80
ATR_GAP = 0.013          # order.initial_trailing_pct — the 1.3% shown in the UI
HIGH = 294.29            # high-water reached (+1.90%)
TRIGGER = 291.73         # HIGH x (1 - 1.3% x 0.67)
LOCK = 0.0075            # config.risk.profit_lock_threshold, US
DISARM_PRICE = ENTRY * (1 + LOCK)   # 290.97 — old disarm point


# US net break-even: 0.02% fees + one 0.1% slippage leg. Passed explicitly so
# these US-shaped cases don't inherit IN's 1.3% via the config default.
BREAKEVEN = 0.0012


def _params(**kw):
    base = dict(
        stop_loss_pct=0.025,
        take_profit_pct=9.99,
        profit_lock_threshold=LOCK,
        trailing_gap_base=0.008,
        round_trip_cost_pct=0.0022,
        net_breakeven_pct=BREAKEVEN,
    )
    base.update(kw)
    return SimParams(**base)


def _grmn(high=HIGH):
    """The GRMN position with its trail already ratcheted to `high`."""
    return _Position(
        entry_ts="t0",
        entry_price=ENTRY,
        stop_loss_price=round(ENTRY * (1 - 0.0151), 2),   # 284.43, as displayed
        take_profit_price=0.0,
        initial_trailing_pct=ATR_GAP,
        high_water=high,
        lock_armed=True,
    )


# ------------------------------------------------------- the regression itself

def test_price_skipping_the_old_window_still_exits():
    """The exact failure: 293.42 -> 290.83, straight past [290.97, 291.73].

    290.83 is BELOW the old disarm price, so the pre-fix code took the `else`
    branch, reset the high and returned None. It must now sell.
    """
    pos = _grmn()
    assert simulate_exit(pos, 293.42, _params()) is None   # above trigger, holds
    assert pos.high_water == pytest.approx(HIGH)

    reason = simulate_exit(pos, 290.83, _params())
    assert reason == "TRAILING_STOP", (
        "price is below the 291.73 trigger and still above entry — must exit "
        "on the trail, not survive to the -1.5% hard stop"
    )


def test_high_water_is_never_lowered():
    """The destructive half of the bug: the peak must survive a dip."""
    pos = _grmn()
    for price in (293.42, 291.90, 290.83, 289.50):
        simulate_exit(pos, price, _params())
        assert pos.high_water == pytest.approx(HIGH), (
            f"high-water was lowered at {price} — the peak is a one-way ratchet"
        )


def test_dip_below_threshold_does_not_disarm():
    pos = _grmn(high=ENTRY * 1.009)     # +0.9%: armed, but only just
    # +0.5% — under the 0.75% arm threshold but still above the break-even
    # floor, so no exit fires and we can observe the latch directly.
    assert simulate_exit(pos, ENTRY * 1.005, _params()) is None
    assert pos.lock_armed is True


# --------------------------------------------------- the unreachable dead zone

@pytest.mark.parametrize("gain_from_high", [0.008, 0.010, 0.012, 0.015])
def test_trailing_stop_is_reachable_in_the_former_dead_zone(gain_from_high):
    """Below ~+1.7% the old gate disarmed above the trigger, so the trail could
    never fire. Every one of these must now produce an exit."""
    high = ENTRY * (1 + gain_from_high)
    pos = _grmn(high=high)
    base_gap = max(ATR_GAP, 0.008)
    mult = 0.67 if gain_from_high >= 0.01 else 0.83
    trigger = max(high * (1 - base_gap * mult), ENTRY * (1 + BREAKEVEN))

    reason = simulate_exit(pos, trigger - 0.01, _params())
    assert reason in ("TRAILING_STOP", "STOP_LOSS"), (
        f"gain_from_high={gain_from_high:.1%} produced no exit at "
        f"{trigger - 0.01:.2f} (trigger {trigger:.2f})"
    )


def test_break_even_floor_is_now_live():
    """The floor at entry x (1 + cost) sat below the old disarm price and could
    never bind. With the latch it must stop a winner becoming a net loser."""
    params = _params()
    floor = ENTRY * (1 + params.net_breakeven_pct)      # 289.15
    assert floor < DISARM_PRICE, "precondition: floor was unreachable before"

    # A tiny high means the tier trigger sits BELOW the floor, so the floor binds.
    pos = _grmn(high=ENTRY * 1.008)
    reason = simulate_exit(pos, floor - 0.01, params)
    assert reason is not None, "break-even floor did not fire"


# ------------------------------------------------------------ arming behaviour

def test_lock_does_not_arm_below_threshold():
    """Patience phase must be preserved — a position that never reached the
    threshold is protected by the hard stop only, and is free to oscillate."""
    pos = _Position(
        entry_ts="t0", entry_price=ENTRY,
        stop_loss_price=ENTRY * (1 - 0.025),
        take_profit_price=0.0,
        initial_trailing_pct=ATR_GAP,
        high_water=ENTRY,
    )
    # Drift up to just under the threshold, then back down.
    assert simulate_exit(pos, ENTRY * 1.005, _params()) is None
    assert pos.lock_armed is False
    assert simulate_exit(pos, ENTRY * 0.999, _params()) is None
    assert pos.lock_armed is False


def test_lock_arms_exactly_at_threshold():
    pos = _Position(
        entry_ts="t0", entry_price=ENTRY,
        stop_loss_price=ENTRY * (1 - 0.025),
        take_profit_price=0.0,
        initial_trailing_pct=ATR_GAP,
        high_water=ENTRY,
    )
    simulate_exit(pos, ENTRY * (1 + LOCK), _params())
    assert pos.lock_armed is True


def test_hard_stop_still_wins_when_price_gaps_through():
    """A gap straight past the trail to below the hard stop must report
    STOP_LOSS, not TRAILING_STOP — the label drives exit-reason analytics."""
    pos = _grmn()
    assert simulate_exit(pos, 280.00, _params()) == "STOP_LOSS"


# ----------------------------------------------- live executor parity (latch)

def test_executor_latch_survives_a_state_roundtrip(tmp_path, monkeypatch):
    """_lock_armed must persist, or a restart silently disarms a locked trail."""
    import json

    from order_executor import OrderExecutor

    class _Stub:
        def get_positions(self): return {}

    monkeypatch.setattr(OrderExecutor, "_get_state_path",
                        lambda self: str(tmp_path / "executor_state_TEST.json"))
    ex = OrderExecutor(_Stub())
    ex._lock_armed["GRMN"] = True
    ex._trailing_high["GRMN"] = HIGH
    ex._dump_state()

    on_disk = json.loads((tmp_path / "executor_state_TEST.json").read_text())
    assert on_disk["lock_armed"]["GRMN"] is True

    revived = OrderExecutor(_Stub())
    assert revived._lock_armed.get("GRMN") is True
    assert revived._trailing_high.get("GRMN") == pytest.approx(HIGH)


def test_executor_latch_absent_in_legacy_state_file(tmp_path, monkeypatch):
    """State written before the latch existed has no `lock_armed` key; loading
    it must not raise, and must leave the position simply unarmed."""
    import json

    from order_executor import OrderExecutor

    class _Stub:
        def get_positions(self): return {}

    path = tmp_path / "executor_state_TEST.json"
    path.write_text(json.dumps({"open_orders": {}, "trailing_high": {"X": 10.0}}))
    monkeypatch.setattr(OrderExecutor, "_get_state_path", lambda self: str(path))

    ex = OrderExecutor(_Stub())
    assert ex._lock_armed == {}
    assert ex._trailing_high["X"] == 10.0


# ------------------------- arm threshold must clear net break-even (IN) ------

def test_arm_threshold_never_sits_below_net_breakeven():
    """The second defect: config's IN threshold (+1.0%) was set when IN
    friction was believed to be ~0.1%. At the real 1.215% it sat BELOW net
    break-even (1.315%), so arming at +1.0% put the break-even floor ABOVE the
    price and sold instantly for +1.00% gross = -0.41% NET. Every IN winner was
    exited at a guaranteed loss the moment it became a winner.
    """
    from trading_costs import PROFIT_LOCK_ARM_MULTIPLE, profit_lock_arm_pct

    in_breakeven = 0.01315          # 1.215% fees + one 0.1% slippage leg
    arm = profit_lock_arm_pct(3800.0, 0.010, market="IN")
    assert arm > in_breakeven, (
        "arming at or below net break-even guarantees an instant net-loss exit"
    )
    assert arm == pytest.approx(in_breakeven * PROFIT_LOCK_ARM_MULTIPLE, rel=0.02)


def test_us_arm_threshold_is_unchanged_by_the_cost_floor():
    """US break-even is 0.12%, far under the configured 0.75%, so the cost
    floor must not disturb US behaviour at all."""
    from trading_costs import profit_lock_arm_pct

    assert profit_lock_arm_pct(30.0, 0.0075, market="US") == pytest.approx(0.0075)


def test_in_position_no_longer_insta_exits_on_arming():
    """End-to-end on IN numbers: arming must not immediately trigger."""
    in_breakeven = 0.01315
    params = SimParams(
        stop_loss_pct=0.025, take_profit_pct=9.99,
        profit_lock_threshold=0.010,          # the too-low config value
        trailing_gap_base=0.010,
        round_trip_cost_pct=0.01415,
        net_breakeven_pct=in_breakeven,
    )
    entry = 3800.0
    pos = _Position(
        entry_ts="t0", entry_price=entry,
        stop_loss_price=entry * (1 - 0.025),
        take_profit_price=0.0,
        initial_trailing_pct=0.013,
        high_water=entry,
    )
    # +1.0% used to arm AND sell in the same tick, booking -0.41% net.
    assert simulate_exit(pos, entry * 1.010, params) is None
    assert pos.lock_armed is False, "must not arm below net break-even"

    # Arms only once genuinely past break-even, and does not fire on arming.
    armed_px = entry * (1 + in_breakeven * 1.25)
    assert simulate_exit(pos, armed_px, params) is None
    assert pos.lock_armed is True
    # And the level it protects is a NET GAIN, not a net loss.
    assert (armed_px / entry - 1) > in_breakeven


def test_net_breakeven_charges_slippage_once_not_twice():
    """round_trip_cost_pct adds slippage for both legs; an exit owes only the
    exit leg, since entry slippage is already inside entry_price."""
    from trading_costs import (ASSUMED_SLIPPAGE_PER_LEG, net_breakeven_pct,
                               round_trip_cost_pct)

    n = 3800.0
    fees = round_trip_cost_pct(n, market="IN", include_slippage=False)
    assert net_breakeven_pct(n, market="IN") == pytest.approx(
        fees + ASSUMED_SLIPPAGE_PER_LEG)
    assert net_breakeven_pct(n, market="IN") < round_trip_cost_pct(n, market="IN")
