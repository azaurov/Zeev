"""The nightly quiet window for the startup greeting.

A reboot at 3am announcing itself into a dark house is the problem, and
restarts are not rare -- every deploy is one, and Restart=on-failure means an
unattended crash-restart is too. The first fix (2026-08-xx) made the greeting
quieter (40%) rather than silent; that still spoke audibly every time the
service restarted overnight, so it was tightened (2026-09-03) to skip the
greeting outright inside quiet hours -- see run_device_mode's
`_greeting_silent = _in_quiet_hours()`. Ongoing conversation once the device
is actually woken is untouched: _scheduled_volume still returns a real,
audible level overnight (_STARTUP_VOLUME, not silence) -- only the
unprompted startup announcement is suppressed.
"""
import pytest


@pytest.mark.parametrize("hour", [22, 23, 0, 1, 3, 5, 7])
def test_inside_the_window(zeev, hour):
    """The window WRAPS midnight. The obvious `START <= h < END` is never true
    for 22->8, so the feature would silently do nothing every single night and
    present as a volume bug rather than a logic bug."""
    assert zeev._in_quiet_hours(hour), hour


@pytest.mark.parametrize("hour", [8, 9, 12, 17, 20, 21])
def test_outside_the_window(zeev, hour):
    assert not zeev._in_quiet_hours(hour), hour


def test_boundaries_are_half_open(zeev):
    """22:00 is quiet, 08:00 is not -- so the morning greeting is full volume."""
    assert zeev._in_quiet_hours(zeev._QUIET_START)
    assert not zeev._in_quiet_hours(zeev._QUIET_END)


def test_non_wrapping_window_still_works(zeev, monkeypatch):
    """A window that does not cross midnight must use the plain comparison."""
    monkeypatch.setattr(zeev, "_QUIET_START", 1)
    monkeypatch.setattr(zeev, "_QUIET_END", 5)
    assert zeev._in_quiet_hours(3)
    assert not zeev._in_quiet_hours(6)
    assert not zeev._in_quiet_hours(23)


def test_equal_bounds_disable_rather_than_mute_all_day(zeev, monkeypatch):
    """START == END is an empty window, not a 24-hour one. Reading it the other
    way would quiet every greeting forever from a one-character typo."""
    monkeypatch.setattr(zeev, "_QUIET_START", 8)
    monkeypatch.setattr(zeev, "_QUIET_END", 8)
    for h in (0, 8, 15, 22):
        assert not zeev._in_quiet_hours(h), h


def test_quiet_volume_constant_was_removed_not_orphaned(zeev):
    """_QUIET_VOLUME (the old "speak the greeting quieter" level) no longer
    has any reader now that quiet hours skip the greeting outright -- this
    guards against it quietly coming back as dead config nobody reads."""
    assert not hasattr(zeev, "_QUIET_VOLUME")


# ---------------------------------------------------------------------------
# Day/night volume schedule
#
# Before this, noon and 9pm played at the identical flat _STARTUP_VOLUME --
# there was no daytime tier at all, only the nightly dip -- and it read as
# the device being quiet all day (reported live 2026-08-03).
# ---------------------------------------------------------------------------

def test_day_volume_is_louder_than_baseline(zeev):
    assert zeev._DAY_VOLUME > zeev._STARTUP_VOLUME


@pytest.mark.parametrize("hour", [8, 9, 12, 17, 20, 21])
def test_scheduled_volume_is_loud_during_the_day(zeev, hour):
    assert zeev._scheduled_volume(hour) == zeev._DAY_VOLUME


@pytest.mark.parametrize("hour", [22, 23, 0, 1, 3, 5, 7])
def test_scheduled_volume_falls_back_overnight(zeev, hour):
    """Overnight non-greeting turns (e.g. a 2am question someone actually
    asked) keep the pre-existing _STARTUP_VOLUME baseline -- quiet hours only
    suppress the unprompted startup greeting, not real conversation."""
    assert zeev._scheduled_volume(hour) == zeev._STARTUP_VOLUME


# ---------------------------------------------------------------------------
# Greeting bucket
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hour,expected", [
    (22, "night"), (23, "night"), (0, "night"), (2, "night"), (4, "night"),
    (5, "morning"), (8, "morning"), (11, "morning"),
    (12, "afternoon"), (17, "afternoon"),
    (18, "evening"), (21, "evening"),
])
def test_time_of_day_buckets(zeev, hour, expected):
    """"night" wraps midnight and so is tested first. The plain `hour < 12`
    chain this replaced called 01:42 "morning" and greeted a dark house with
    "Good morning, Alex." -- observed live."""
    assert zeev._time_of_day(hour) == expected, hour


def test_night_and_quiet_windows_differ_on_purpose(zeev):
    """Quiet hours run to 08:00 because volume should stay down through early
    morning; the greeting stops calling it night at 05:00, since being told
    "it's late" at 07:00 is simply wrong."""
    assert zeev._time_of_day(6) == "morning"
    assert zeev._in_quiet_hours(6)


def test_every_bucket_has_a_greeting(zeev):
    """A missing key would raise KeyError inside the startup thread, which
    would take out _greeting_done with it and leave the wake listener waiting
    forever -- the device deaf, with a traceback nowhere near the cause."""
    buckets = {zeev._time_of_day(h) for h in range(24)}
    assert buckets == {"night", "morning", "afternoon", "evening"}
