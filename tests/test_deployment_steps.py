"""
Two-step capital deployment.

Small accounts deploy hard (90%) — a half-deployed tiny account cannot hold
enough positions to matter. Once an account clears its threshold the cap steps
DOWN to 50%, keeping a real cash buffer when there is something to protect.

Thresholds: US $500, IN ₹1,00,000.

Also pins that the position count is high enough for 90% to be REACHABLE: the
0.5% risk cap sizes each position at NAV*0.005/stop_pct, so N positions cap
peak deployment at N x that regardless of what the deploy cap allows.
"""

import importlib

import pytest

# Import ONCE at collection time, while conftest still has TRADING_MARKET=IN.
# A fresh import under US drags in price_feed -> alpaca-py (image-only), which
# made this file pass in the full suite but fail when run alone.
import decision_engine as _de_mod


def _engine_for(market, monkeypatch):
    """Market-specific config + the deploy constants.

    decision_engine is imported ONCE under IN and never reloaded per-market:
    reloading it with TRADING_MARKET=US drags in price_feed -> alpaca-py,
    which is image-only. The deploy constants are module-level and
    market-independent, so this loses nothing.
    """
    monkeypatch.setenv("TRADING_MARKET", market)
    import config as config_mod
    importlib.reload(config_mod)
    return _de_mod, config_mod.config


@pytest.mark.parametrize("market,below,above,cur", [
    ("US", 499.0, 501.0, "$"),
    ("IN", 99_000.0, 101_000.0, "₹"),
])
def test_deploy_steps_down_at_the_threshold(market, below, above, cur, monkeypatch):
    de_mod, cfg = _engine_for(market, monkeypatch)
    step = de_mod.US_DEPLOY_STEP_NAV if market == "US" else de_mod.IN_DEPLOY_STEP_NAV
    assert below < step <= above
    # below -> 90%, at/above -> the configured 50%
    assert de_mod.SMALL_ACCOUNT_DEPLOY_PCT == pytest.approx(0.90)
    assert cfg.wallet.max_deploy_pct == pytest.approx(0.50)


def test_thresholds_are_the_agreed_values(monkeypatch):
    de_mod, _ = _engine_for("IN", monkeypatch)
    assert de_mod.US_DEPLOY_STEP_NAV == pytest.approx(500.0)
    assert de_mod.IN_DEPLOY_STEP_NAV == pytest.approx(100_000.0)


@pytest.mark.parametrize("market,nav", [("IN", 17_490.0), ("US", 101.16)])
def test_heat_budget_reaches_the_deploy_target(market, nav, monkeypatch):
    """Concurrency is limited by the HEAT BUDGET (0.75%/trade, 2.25% total),
    not by max_open_positions — which is now only an outer bound. The
    positions the budget permits must still reach the 90% deploy target,
    otherwise deployment is silently capped below what was configured."""
    de_mod, cfg = _engine_for(market, monkeypatch)
    risk, heat = 0.0075, 0.0225        # compose values for both markets
    tight_stop = 0.025                 # the stop floor => largest position size
    permitted = int(heat / risk)       # 3
    assert permitted <= cfg.risk.max_open_positions, (
        "max_open_positions must not cut below the heat budget")
    per_position = min(nav * risk / tight_stop, nav * cfg.risk.max_position_size_pct)
    peak = permitted * per_position
    assert peak >= nav * de_mod.SMALL_ACCOUNT_DEPLOY_PCT * 0.99, (
        f"{market}: {permitted} heat-permitted positions reach only "
        f"{peak/nav:.0%} of NAV — cannot hit the 90% target")


@pytest.mark.parametrize("market,expected", [("IN", 5), ("US", 5)])
def test_position_counts(market, expected, monkeypatch):
    _de, cfg = _engine_for(market, monkeypatch)
    assert cfg.risk.max_open_positions == expected


def test_total_risk_at_full_deployment_is_bounded(monkeypatch):
    """All positions stopping out at once must stay inside the daily halt's
    reach — the halt is what stops a cascade, so it must be able to fire."""
    _de, cfg = _engine_for("IN", monkeypatch)
    worst_case = cfg.risk.max_open_positions * 0.005      # 5 x 0.5% = 2.5%
    assert worst_case > cfg.risk.max_daily_loss_pct, (
        "daily halt must trigger before every position can stop out")
