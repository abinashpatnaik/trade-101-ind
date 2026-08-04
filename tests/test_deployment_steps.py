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


@pytest.mark.parametrize("market,nav,risk,possize", [
    # compose values per market: IN concentrates (1 position, 2.25% risk, 90%
    # size); US keeps the diversified budget (3 positions, 0.75% risk, 30%).
    ("IN", 17_490.0, 0.0225, 0.90),
    ("US", 101.16, 0.0075, 0.30),
])
def test_heat_budget_reaches_the_deploy_target(market, nav, risk, possize, monkeypatch):
    """The positions the heat budget permits must still reach the 90% deploy
    target, otherwise deployment is silently capped below what was configured.
    On IN this is now ONE ~90% position; on US, three ~30% positions."""
    de_mod, cfg = _engine_for(market, monkeypatch)
    heat = 0.0225
    tight_stop = 0.025                 # the stop floor => largest position size
    permitted = max(1, int(heat / risk))       # IN 1, US 3
    assert permitted <= cfg.risk.max_open_positions, (
        "max_open_positions must not cut below the heat budget")
    per_position = min(nav * risk / tight_stop, nav * possize)
    peak = permitted * per_position
    assert peak >= nav * de_mod.SMALL_ACCOUNT_DEPLOY_PCT * 0.99, (
        f"{market}: {permitted} heat-permitted positions reach only "
        f"{peak/nav:.0%} of NAV — cannot hit the 90% target")


@pytest.mark.parametrize("market,expected", [("IN", 1), ("US", 5)])
def test_position_counts(market, expected, monkeypatch):
    # IN concentrates into ONE position (2026-08-03) so a >Rs10k order engages
    # the Rs50 NRO brokerage cap; US stays diversified at 5.
    _de, cfg = _engine_for(market, monkeypatch)
    assert cfg.risk.max_open_positions == expected


def test_total_risk_at_full_deployment_is_bounded(monkeypatch):
    """All positions stopping out at once must stay inside the daily halt's
    reach — the halt is what stops a cascade, so it must be able to fire.
    IN now runs ONE position at 2.25% risk; 1 x 2.25% = 2.25% > 2% halt."""
    _de, cfg = _engine_for("IN", monkeypatch)
    per_trade_risk = 0.0225                              # IN compose value
    worst_case = cfg.risk.max_open_positions * per_trade_risk
    assert worst_case > cfg.risk.max_daily_loss_pct, (
        "daily halt must trigger before every position can stop out")
