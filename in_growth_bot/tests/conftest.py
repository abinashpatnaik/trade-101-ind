import pytest

from in_growth_bot.config import settings


@pytest.fixture(autouse=True)
def defaults(monkeypatch, tmp_path):
    """Every test: simulation mode with default rules, files in a temp dir."""
    for k, v in dict(mode="sim", data_dir=str(tmp_path / "data"), shared_dir=str(tmp_path / "shared"),
                     tax_aware=True, harvest_exemption=True, include_etfs=False, sell_only_in_profit=True,
                     regime_action="pause", no_new_buys=False, starting_cash=0.0,
                     monthly_contribution=25000.0, contribution_day=1).items():
        monkeypatch.setattr(settings, k, v)
