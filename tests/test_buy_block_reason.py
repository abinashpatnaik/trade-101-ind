"""
Disambiguated BUY-veto reason shown on the dashboard.

Bug this pins (live, 2026-07-24): DIACABS was approved AND flat, yet the
dashboard showed it "GATED — Not in today's approved targets — exit-only". Cause:
`buy_eligible` is False for two distinct reasons — an approved name paused near
the close, versus a name genuinely off today's targets — and both collapsed to
one hardcoded string that mislabelled the first as the second.

The veto BEHAVIOUR is unchanged (buys still don't fire near the close); only the
reason TEXT is corrected, so an approved stock never again reads as un-approved.
"""

from agents.trader import TradingAgent


def test_approved_name_near_close_is_not_labelled_off_target():
    """The exact DIACABS case: in today's targets, buys paused near close."""
    reason = TradingAgent._buy_block_reason(in_targets=True, buys_allowed=False)
    assert "paused" in reason.lower()
    assert "close" in reason.lower()
    assert "not in today" not in reason.lower(), (
        "an approved stock must never read as 'not in approved targets'")


def test_off_target_name_keeps_the_original_message():
    """A held name that dropped off today's targets is genuinely off-list."""
    reason = TradingAgent._buy_block_reason(in_targets=False, buys_allowed=False)
    assert reason == "Not in today's approved targets — exit-only"


def test_off_target_message_is_independent_of_buy_window():
    """Off-list is off-list whether or not entries are open."""
    assert (TradingAgent._buy_block_reason(False, True)
            == TradingAgent._buy_block_reason(False, False))


def test_approved_and_buys_open_still_yields_a_string():
    """Defensive: the helper is only called when ineligible, but must never
    return None (that would fall back to the old hardcoded reason)."""
    r = TradingAgent._buy_block_reason(in_targets=True, buys_allowed=True)
    assert isinstance(r, str) and r


def test_reason_names_the_buffer_window():
    """The paused message surfaces the actual configured buffer, not a guess."""
    from config import config
    reason = TradingAgent._buy_block_reason(True, False)
    assert str(config.market.no_entry_buffer_minutes) in reason
