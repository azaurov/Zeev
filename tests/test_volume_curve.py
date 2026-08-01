"""Volume percentage -> WM8960 raw value.

The control is 0-127 in 1 dB steps (raw 121 = 0 dB, raw 127 = +6 dB), and
anything under ~48 is below -73 dB, i.e. off. The old `pct/100*127` therefore
made the slider linear in DECIBELS: ~12.7 dB per 10%, more than doubling
perceived loudness per notch, with the bottom third inaudible.

Measured on the Pi 2026-08-01, which is what Alex was hearing:
    "40%" -> raw  51 -> -70 dB   (silent, reported as "almost muted")
    "90%" -> raw 114 ->  -7 dB   (reported as "normal")
"""
import pytest

# (percent, expected raw). raw - 121 == dB.
CURVE = [(0, 0), (1, 81), (10, 85), (25, 92), (40, 99),
         (50, 104), (70, 113), (80, 118), (90, 122), (100, 127)]


@pytest.mark.parametrize("pct,raw", CURVE)
def test_curve(zeev, pct, raw):
    assert zeev._pct_to_raw(pct) == raw, f"{pct}% -> {zeev._pct_to_raw(pct)}, want {raw}"


def test_zero_is_true_mute(zeev):
    """0 must be silence, not merely the bottom of the dB window."""
    assert zeev._pct_to_raw(0) == 0


def test_nothing_lands_in_the_dead_zone(zeev):
    """Below raw ~48 the chip is under -73 dB and simply off. No non-zero
    percentage may map there -- that dead zone swallowing the bottom third of
    the range is the bug."""
    for pct in range(1, 101):
        assert zeev._pct_to_raw(pct) >= 48, f"{pct}% -> {zeev._pct_to_raw(pct)}"


def test_monotonic_and_bounded(zeev):
    vals = [zeev._pct_to_raw(p) for p in range(0, 101)]
    assert vals == sorted(vals)
    assert max(vals) == 127 and min(vals) == 0


def test_out_of_range_is_clamped(zeev):
    assert zeev._pct_to_raw(-10) == 0
    assert zeev._pct_to_raw(500) == 127


def test_comfortable_is_near_70_and_loud_near_90(zeev):
    """The point of the recalibration: -8 dB (what used to need 90%) sits at
    70%, and 90% is now genuinely loud rather than merely normal."""
    assert zeev._pct_to_raw(70) - 121 == -8
    assert zeev._pct_to_raw(90) - 121 == 1


def test_startup_default_preserves_the_old_loudness(zeev):
    """Default moved 90 -> 70 so the recalibration does not silently make the
    device louder than it was: both are -8/-7 dB."""
    assert zeev._STARTUP_VOLUME == 70
    assert abs((zeev._pct_to_raw(zeev._STARTUP_VOLUME) - 121) - (-7)) <= 1
