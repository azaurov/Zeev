"""Street/POI/home-level location detail, behind `needs_gps`, never ambient.

Nominatim returns the nearest road for any coordinate regardless of fix
quality, so vicinity claims are gated on accuracy separately from the coarser
city/region gate `ambient_place()` already uses. Home is an opt-in via
ZEEV_HOME_LAT/LON -- absent config must mean no claim, never "not home".
"""
import pytest


@pytest.fixture
def _home(zeev):
    saved = (zeev._HOME_LAT, zeev._HOME_LON, zeev._HOME_RADIUS)
    yield
    zeev._HOME_LAT, zeev._HOME_LON, zeev._HOME_RADIUS = saved


def _loc(**kw):
    base = {"lat": 42.14, "lon": -71.11, "accuracy": 11}
    base.update(kw)
    return base


def test_road_hedged_and_accurate_fix_required(zeev):
    assert zeev.vicinity_place(_loc(road="Lincoln Street")) == "approximately on Lincoln Street"
    assert zeev.vicinity_place(_loc(road="Lincoln Street", accuracy=25000)) == ""


def test_poi_wins_over_road(zeev):
    loc = _loc(road="Cambridge Street", poi="Main Plaza")
    assert zeev.vicinity_place(loc) == "near Main Plaza"


def test_no_road_or_poi_is_silent(zeev):
    assert zeev.vicinity_place(_loc()) == ""


def test_no_home_configured_makes_no_claim(zeev, _home):
    zeev._HOME_LAT = zeev._HOME_LON = None
    loc = _loc(lat=42.14, lon=-71.11, road="Lincoln Street")
    assert zeev.vicinity_place(loc) != "at home"


def test_within_home_radius_wins_over_road(zeev, _home):
    zeev._HOME_LAT, zeev._HOME_LON, zeev._HOME_RADIUS = 42.14, -71.11, 100.0
    loc = _loc(lat=42.1401, lon=-71.1101, road="Lincoln Street")
    assert zeev.vicinity_place(loc) == "at home"


def test_outside_home_radius_falls_through_to_road(zeev, _home):
    zeev._HOME_LAT, zeev._HOME_LON, zeev._HOME_RADIUS = 42.14, -71.11, 100.0
    loc = _loc(lat=42.16, lon=-71.13, road="Elm Street")
    assert zeev.vicinity_place(loc) == "approximately on Elm Street"


def test_gps_summary_includes_vicinity(zeev):
    loc = _loc(road="Lincoln Street", city="Fairview", regionName="Massachusetts",
               country="United States", method="wifi+google")
    s = zeev.gps_summary(loc)
    assert s.startswith("approximately on Lincoln Street, Fairview")


def test_gate_matches_street_and_home_phrasing(zeev):
    for text in ["what street am i on", "which road are we on", "am i at home",
                 "are we near home", "what street is this"]:
        assert zeev._GPS_RE.search(text), f"gate missed {text!r}"


def test_malformed_env_float_falls_back_instead_of_raising(zeev, monkeypatch):
    """A hand-edited .env on the Pi has already produced garbled values once
    (the Wyze password `printf` ate). This must degrade, not crash the app."""
    monkeypatch.setenv("ZEEV_HOME_RADIUS", "not-a-number")
    assert zeev._env_float("ZEEV_HOME_RADIUS", 100.0) == 100.0
    monkeypatch.setenv("ZEEV_HOME_LAT", "42.14")
    assert zeev._env_float("ZEEV_HOME_LAT", None) == 42.14


def test_ip_fallback_fix_expires_fast_so_wifi_is_retried_soon(zeev, monkeypatch):
    """An IP fallback (found live 2026-09-03): a too-early post-boot WiFi scan
    returns < 2 cached APs, gps_locate() falls to ~25km-accuracy IP, and used
    to cache that bad fix for the full 30-minute _GPS_CACHE_TTL -- Zeev stated
    a wrong city (Cambridge instead of Canton) for ~40 minutes before the next
    refresh-loop tick happened to land after the TTL. A real WiFi/Google fix
    right after should NOT be blocked by a lingering IP-fix cache entry."""
    saved = dict(zeev._gps_cache)
    zeev._gps_cache.clear()
    try:
        zeev._gps_cache["result"] = {"lat": 42.37, "lon": -71.10, "accuracy": 25000,
                                      "method": "ip", "city": "Cambridge"}
        zeev._gps_cache["ts"] = 1_000_000.0

        monkeypatch.setattr(zeev, "_wifi_scan_aps",
                             lambda: [{"macAddress": "aa:bb", "signalStrength": -60},
                                      {"macAddress": "cc:dd", "signalStrength": -70}])
        monkeypatch.setattr(zeev, "_wifi_geolocate",
                             lambda aps: {"lat": 42.16, "lon": -71.14, "accuracy": 12,
                                          "method": "wifi+google"})

        # Still within the IP TTL -> serves the stale cached IP fix.
        monkeypatch.setattr(zeev.time, "time", lambda: 1_000_000.0 + 60)
        assert zeev.gps_locate()["method"] == "ip"

        # Past the IP TTL (2 min) but well under the normal 30-min TTL ->
        # must retry and pick up the real fix instead of trusting the cache.
        monkeypatch.setattr(zeev.time, "time", lambda: 1_000_000.0 + 150)
        result = zeev.gps_locate()
        assert result["method"] == "wifi+google"
        assert result["accuracy"] == 12
    finally:
        zeev._gps_cache.clear()
        zeev._gps_cache.update(saved)
