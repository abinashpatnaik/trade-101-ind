"""
Calendar-flow agent: the pure decision logic that keeps the LIVE strategy
identical to the VALIDATED one.

The asymmetry under test is deliberate and load-bearing:
  * entries fire ONLY on the exact entry day — a mid-window entry (deploy or
    restart partway through the window) trades an untested partial window, so
    the month is skipped instead;
  * a held position past the exit day is closed at the next opportunity and
    keeps signalling EXIT until the sell succeeds.
No broker, no network — dates and state only.
"""

from datetime import date, timedelta

import pytest

from agents.flow_trader import FlowState, decide, entry_exit_days


def _days(n=22, start=date(2026, 7, 1)):
    """n synthetic consecutive weekdays."""
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = _days()
ENTRY, EXIT = entry_exit_days(DAYS)


# ------------------------------------------------------------- day math
def test_entry_is_seventh_last_and_exit_second_last():
    assert ENTRY == DAYS[-7]
    assert EXIT == DAYS[-2]
    assert ENTRY < EXIT


def test_short_month_yields_no_window():
    assert entry_exit_days(_days(6)) == (None, None)


# ------------------------------------------------------------- decide()
def test_enters_only_on_the_exact_entry_day():
    st = FlowState()
    assert decide(ENTRY, st, ENTRY, EXIT) == "ENTER"
    # one day late = untested partial window -> skip the month entirely
    assert decide(DAYS[-6], st, ENTRY, EXIT) is None
    assert decide(DAYS[-5], st, ENTRY, EXIT) is None
    # and days before the window never enter
    assert decide(DAYS[0], st, ENTRY, EXIT) is None


def test_no_double_entry_same_month():
    st = FlowState(holding_qty=0.0, entered_month=f"{ENTRY.year:04d}-{ENTRY.month:02d}")
    assert decide(ENTRY, st, ENTRY, EXIT) is None


def test_holds_through_the_window():
    st = FlowState(holding_qty=5.0, entered_month=f"{ENTRY.year:04d}-{ENTRY.month:02d}")
    for d in DAYS[-6:-2]:                      # between entry and exit
        assert decide(d, st, ENTRY, EXIT) is None


def test_exits_on_exit_day_and_keeps_retrying():
    st = FlowState(holding_qty=5.0, entered_month=f"{ENTRY.year:04d}-{ENTRY.month:02d}")
    assert decide(EXIT, st, ENTRY, EXIT) == "EXIT"
    # sell failed (still holding): next session must retry, not give up
    assert decide(DAYS[-1], st, ENTRY, EXIT) == "EXIT"


def test_month_rollover_forces_exit():
    """Agent down over the turn: position from June must be closed in July."""
    st = FlowState(holding_qty=5.0, entered_month="2026-06")
    assert decide(DAYS[0], st, ENTRY, EXIT) == "EXIT"


def test_flat_and_no_window_does_nothing():
    assert decide(DAYS[0], FlowState(), None, None) is None


# ------------------------------------------------------------- state file
def test_state_round_trip(tmp_path):
    p = str(tmp_path / "flow_state.json")
    st = FlowState(holding_qty=3.21, entered_month="2026-08", exited_month="2026-07")
    st.save(p)
    back = FlowState.load(p)
    assert back == st


def test_state_load_missing_file_is_flat(tmp_path):
    st = FlowState.load(str(tmp_path / "nope.json"))
    assert st.holding_qty == 0.0 and st.entered_month == ""


# ------------------------------------------------------- trader ignore list
def test_alpaca_ignore_parsing(monkeypatch):
    # alpaca-py lives only in the Docker images; skip locally, run in-container.
    pytest.importorskip("alpaca")
    from alpaca_connector import AlpacaConnector

    monkeypatch.setenv("ALPACA_IGNORE_SYMBOLS", "tlt, EDV ,")
    assert AlpacaConnector.ignored_symbols() == {"TLT", "EDV"}
    monkeypatch.setenv("ALPACA_IGNORE_SYMBOLS", "")
    assert AlpacaConnector.ignored_symbols() == set()
