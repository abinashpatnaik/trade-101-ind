import sqlite3

import pytest

from uk_growth_bot import broker as B
from uk_growth_bot.config import Settings
from uk_growth_bot.ledger import Ledger


@pytest.mark.parametrize("mode,env,expected_env,uses_broker", [
    ("sim", "live", "demo", False),
    ("paper", "live", "demo", True),     # paper can never reach real money
    ("paper", "demo", "demo", True),
    ("live", "demo", "demo", True),      # live on the practice account
    ("live", "live", "live", True),      # the only real-money combination
])
def test_modes(monkeypatch, mode, env, expected_env, uses_broker):
    monkeypatch.setenv("UK_TRADING_MODE", mode)
    monkeypatch.setenv("T212_ENV", env)
    s = Settings()
    assert s.t212_env == expected_env and s.uses_broker is uses_broker
    assert s.simulated_funding is (expected_env == "demo")
    assert ("LIVE (real money)" == s.mode_label) is (expected_env == "live")


def test_paper_mode_sends_orders_to_trading212(monkeypatch):
    monkeypatch.setattr(B.settings, "mode", "paper")
    assert isinstance(B.make_broker(), B.Trading212Broker)
    monkeypatch.setattr(B.settings, "mode", "sim")
    assert isinstance(B.make_broker(), B.SimBroker)


def test_old_paper_records_become_sim(tmp_path):
    path = str(tmp_path / "old.db")
    Ledger(path=path, mode="sim")  # creates the schema
    with sqlite3.connect(path) as c:  # recreate a pre-rename database
        c.execute("DELETE FROM state")
        c.execute("INSERT INTO txns (day, symbol, side, quantity, price, fees, reason, order_id, mode)"
                  " VALUES ('2026-10-05','VWRP.L','BUY',1,100,0,'','sim','paper')")
        c.execute("INSERT INTO cash_flows (day, amount, kind, note, mode)"
                  " VALUES ('2026-10-01',200,'contribution','monthly','paper')")
        c.execute("INSERT INTO state (key, value) VALUES ('paper:last_run', '\"2026-10-07\"')")
    paper, sim = Ledger(path=path, mode="paper"), Ledger(path=path, mode="sim")
    assert not paper.txns() and paper.net_contributions() == 0 and paper.get_state("last_run") is None
    assert len(sim.txns()) == 1 and sim.net_contributions() == 200
    assert sim.get_state("last_run") == "2026-10-07"
    Ledger(path=path, mode="paper")  # reopening must not re-run the migration
    assert len(Ledger(path=path, mode="sim").txns()) == 1
