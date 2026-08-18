"""Scheduled good-morning/goodnight greeting gate.

The scheduler loop that calls this lives inside run_device_mode (needs the HAT
to import), so the fire/no-fire decision is a module-level pure predicate,
same shape as _should_plead_battery -- pinned here for the same reason: a
greeting that never fires and one that fires every poll both look like
"nothing to see" in the code.
"""
from datetime import datetime


def _dt(y, m, d, h, mi):
    return datetime(y, m, d, h, mi)


# 2026-08-17 is a Monday.
MON_630 = _dt(2026, 8, 17, 6, 30)
MON_2300 = _dt(2026, 8, 17, 23, 0)
SAT_630 = _dt(2026, 8, 22, 6, 30)
SUN_2300 = _dt(2026, 8, 23, 23, 0)


def test_fires_morning_at_target(zeev):
    due = dict(zeev._due_greetings(MON_630, {}))
    assert "morning" in due
    assert "night" not in due


def test_fires_night_at_target(zeev):
    due = dict(zeev._due_greetings(MON_2300, {}))
    assert "night" in due
    assert "morning" not in due


def test_silent_on_saturday(zeev):
    assert zeev._due_greetings(SAT_630, {}) == []


def test_silent_on_sunday(zeev):
    assert zeev._due_greetings(SUN_2300, {}) == []


def test_fires_within_catchup_window(zeev):
    """A poll loop that starts a couple minutes into the window still
    catches the target -- exact-second alignment isn't required."""
    late = _dt(2026, 8, 17, 6, 33)
    due = dict(zeev._due_greetings(late, {}))
    assert "morning" in due


def test_silent_once_window_passes(zeev):
    """But a restart well after the target must not fire hours late."""
    much_later = _dt(2026, 8, 17, 9, 0)
    due = dict(zeev._due_greetings(much_later, {}))
    assert "morning" not in due


def test_no_repeat_same_day(zeev):
    """Already fired today -- last_fired blocks a second fire even though
    we're still inside the catch-up window."""
    last_fired = {"morning": MON_630.date()}
    due = dict(zeev._due_greetings(MON_630, last_fired))
    assert "morning" not in due


def test_fires_again_next_weekday(zeev):
    tue_630 = _dt(2026, 8, 18, 6, 30)
    last_fired = {"morning": MON_630.date()}
    due = dict(zeev._due_greetings(tue_630, last_fired))
    assert "morning" in due


# --- the lines themselves ---------------------------------------------------

def test_every_goodmorning_pair_names_everyone(zeev):
    for zeev_line, sarina_line in zeev._GOODMORNING_LINES:
        both = zeev_line + " " + sarina_line
        for who in zeev._GOODNIGHT_HOUSEHOLD:
            assert who in both, f"{who!r} missing from pair: {both!r}"


def test_schedule_is_670_and_2300(zeev):
    schedule = {(h, m): label for h, m, _lines, label in zeev._GREETING_SCHEDULE}
    assert schedule[(6, 30)] == "morning"
    assert schedule[(23, 0)] == "night"
