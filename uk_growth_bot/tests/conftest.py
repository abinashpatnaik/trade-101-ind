import pytest

from uk_growth_bot.config import settings


def _apply(monkeypatch, **kw):
    for k, v in kw.items():
        monkeypatch.setattr(settings, k, v)


@pytest.fixture(autouse=True)
def isa_t212(monkeypatch):
    """Default for every test: Trading 212 Stocks & Shares ISA, independent of the shell's env."""
    _apply(monkeypatch, mode="sim", t212_env="demo", broker="trading212", account_type="isa", commission_min=0.0,
           commission_pct=0.0, min_order_value=10.0, cash_reserve=1.0, qty_decimals=2)


@pytest.fixture
def gia_ibkr(monkeypatch):
    _apply(monkeypatch, broker="ibkr", account_type="gia", commission_min=3.0, commission_pct=0.0005,
           min_order_value=100.0, cash_reserve=5.0)
