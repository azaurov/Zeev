"""The system prompt must carry the wall clock on every turn.

Without it the model has no clock at all and confabulates rather than
declining. Observed live 2026-07-29, three consecutive turns: "I don't have the
current time", then "it's 8:45 AM", then -- after being told to recalibrate --
"it's currently 2:45 PM". The Pi's own clock read 21:03 EDT throughout, and
timedatectl was correctly set to America/New_York. The only wall clock anywhere
in the file was inside the tool-calling prompt, which ordinary chat never sees.
"""
import re


def test_now_str_includes_timezone(zeev):
    """The zone is the part that had to be there, and the easiest to lose.

    `datetime.now()` is naive, so `%Z` formats as '' and the zone silently
    disappears -- the string still looks fine, which is what makes it a trap.
    """
    s = zeev._now_str()
    assert s.strip(), "clock string is empty"
    assert re.search(r"\b[A-Z]{2,5}\b$", s), f"no timezone at end of {s!r}"


def test_now_str_has_date_and_time(zeev):
    s = zeev._now_str()
    assert re.search(r"\b(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\b", s), s
    assert re.search(r"\b\d{4}\b", s), f"no year in {s!r}"
    assert re.search(r"\d{1,2}:\d{2}\s*(AM|PM)", s), f"no clock time in {s!r}"


def test_clock_is_in_every_prompt(zeev):
    """Not gated on any regex -- an ungated turn is the case that broke."""
    for text in ["hello", "tell me a joke", "what time is it", "как дела"]:
        p = zeev._build_system_prompt(text)
        assert "## Right now:" in p, f"clock missing for {text!r}"
        assert zeev._now_str()[:22] in p, f"clock value missing for {text!r}"


# --- ambient location -------------------------------------------------------
#
# Location used to reach the prompt only when needs_gps() matched, so the model
# was blind to where it was on every other turn. Two properties matter: the read
# path must never trigger a scan (a cold gps_locate measured 1.27s on the Pi,
# plus the rescan wait), and a coarse fix must not assert a city -- an IP fix is
# ~25 km and named Ashcroft/Millbrook for a device sitting in Fairview.

import pytest


@pytest.fixture
def _gps(zeev):
    saved = dict(zeev._gps_cache)
    zeev._gps_cache.clear()
    yield zeev._gps_cache
    zeev._gps_cache.clear()
    zeev._gps_cache.update(saved)


def _fix(zeev, gps, **kw):
    loc = {"lat": 42.14, "lon": -71.11, "accuracy": 11, "method": "wifi+google",
           "city": "Fairview", "regionName": "Massachusetts", "country": "United States"}
    loc.update(kw)
    gps["result"] = loc
    gps["ts"] = __import__("time").time()
    return loc


def test_precise_fix_gives_city(zeev, _gps):
    _fix(zeev, _gps)
    assert zeev.ambient_place() == "Fairview, Massachusetts, United States"


def test_coarse_fix_drops_the_city(zeev, _gps):
    """An IP fix names the wrong town; region is true, a wrong city is not."""
    _fix(zeev, _gps, accuracy=25000, method="ip", city="Ashcroft")
    place = zeev.ambient_place()
    assert "Ashcroft" not in place
    assert place == "Massachusetts, United States"


def test_ambient_block_omits_coordinates(zeev, _gps):
    """Coarse name only -- this goes to third-party LLM providers every turn."""
    _fix(zeev, _gps)
    p = zeev._build_system_prompt("hello")
    assert "## Approximate location: Fairview" in p
    assert "42.14" not in p and "-71.11" not in p
    assert "wifi+google" not in p


def test_read_path_never_scans(zeev, _gps, monkeypatch):
    """gps_cached must not fall through to a scan; that would be turn latency."""
    def _boom(*a, **kw):
        raise AssertionError("gps_locate/scan called on the prompt path")
    monkeypatch.setattr(zeev, "_wifi_scan_aps", _boom)
    monkeypatch.setattr(zeev, "_ip_geolocate", _boom)
    assert zeev.gps_cached() is None          # empty cache
    assert zeev.ambient_place() == ""
    zeev._build_system_prompt("hello")        # must not raise


def test_stale_fix_is_dropped_eventually(zeev, _gps):
    _fix(zeev, _gps)
    _gps["ts"] = __import__("time").time() - (zeev._AMBIENT_GPS_MAX_AGE + 60)
    assert zeev.gps_cached() is None


def test_error_fix_yields_no_block(zeev, _gps):
    _gps["result"] = {"error": "no network"}
    _gps["ts"] = __import__("time").time()
    assert zeev.ambient_place() == ""
    assert "## Approximate location:" not in zeev._build_system_prompt("hello")


def test_percent_to_dbm_conversion(zeev):
    """Measured: percent gave 869m accuracy, dBm gave 11m, same 7 APs."""
    assert zeev._nm_percent_to_dbm(74) == -63
    assert zeev._nm_percent_to_dbm(100) == -50
    assert zeev._nm_percent_to_dbm(0) == -100
    for pct in range(0, 101):
        assert -100 <= zeev._nm_percent_to_dbm(pct) <= -20
