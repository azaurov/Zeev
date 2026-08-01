"""Low-battery plea gate.

The monitor that calls this lives inside run_device_mode, which needs the HAT
to import -- so the latch/cooldown/re-arm decision is a module-level pure
predicate and this is where it is pinned. That logic is exactly the kind that
breaks silently: a plea that never fires and a plea that fires every 30 seconds
both look like "nothing to see" in the code.
"""
import pytest

NOW = 1_000_000.0


def test_fires_below_threshold(zeev):
    assert zeev._should_plead_battery(9, False, 0.0, NOW)


def test_fires_at_threshold(zeev):
    """10% is low, not "still fine" -- the boundary is inclusive."""
    assert zeev._should_plead_battery(zeev._LOW_BATTERY_PCT, False, 0.0, NOW)


def test_silent_above_threshold(zeev):
    assert not zeev._should_plead_battery(11, False, 0.0, NOW)
    assert not zeev._should_plead_battery(80, False, 0.0, NOW)


def test_silent_while_charging(zeev):
    """Already plugged in -- the plea has nothing to ask for."""
    assert not zeev._should_plead_battery(5, True, 0.0, NOW)


def test_pleads_when_charge_state_unknown(zeev):
    """PiSugar unreadable -> charging is None. Staying quiet because we cannot
    tell is the wrong way to be wrong; the 2% shutdown treats None the same."""
    assert zeev._should_plead_battery(5, None, 0.0, NOW)


def test_no_repeat_within_cooldown(zeev):
    """Polling is every 30s. Without this the device nags twice a minute all
    the way down to the shutdown."""
    just_spoke = NOW - 30
    assert not zeev._should_plead_battery(9, False, just_spoke, NOW)


def test_renags_after_cooldown(zeev):
    """Firing exactly once at 10% leaves eight silent percent before death."""
    stale = NOW - zeev._LOW_BATTERY_COOLDOWN - 1
    assert zeev._should_plead_battery(9, False, stale, NOW)


def test_no_level_is_silent(zeev):
    assert not zeev._should_plead_battery(None, False, 0.0, NOW)


def test_charging_clears_latch_so_next_unplug_renags(zeev):
    """The monitor resets last_plea to 0 while charging. This is the property
    that makes it work: unplug at 9% right after a top-up and it pleads at
    once rather than sitting out a cooldown it already served."""
    assert zeev._should_plead_battery(9, False, 0.0, NOW)


# --- the lines themselves -------------------------------------------------

def test_every_pair_asks_to_be_plugged_in(zeev):
    """The goodnight-branch rule, applied here: a pair where neither line
    states the request is atmospheric, not a plea."""
    ask = ("plug", "charg", "power", "cable")
    for zeev_line, sarina_line in zeev._LOW_BATTERY_LINES:
        both = (zeev_line + " " + sarina_line).lower()
        assert any(w in both for w in ask), both


def test_every_pair_names_alex(zeev):
    for zeev_line, sarina_line in zeev._LOW_BATTERY_LINES:
        assert "Alex" in zeev_line + sarina_line


def test_lines_format_with_pct(zeev):
    """Zeev's line interpolates the level; Sarina's may not. Either way
    .format(pct=...) must not raise on either half."""
    for zeev_line, sarina_line in zeev._LOW_BATTERY_LINES:
        assert "%" not in zeev_line.format(pct=9), "spoken -- spell out 'percent'"
        assert "%" not in sarina_line.format(pct=9)


def test_threshold_is_above_the_shutdown(zeev):
    """A plea at or below the 2% shutdown point would never be heard."""
    assert zeev._LOW_BATTERY_PCT > 2
