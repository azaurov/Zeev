"""The nightly quiet window for the startup greeting.

A reboot at 3am announcing itself at 90% into a dark house is the problem, and
restarts are not rare -- every deploy is one.
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


def test_quiet_volume_is_below_normal(zeev):
    assert zeev._QUIET_VOLUME < zeev._STARTUP_VOLUME
