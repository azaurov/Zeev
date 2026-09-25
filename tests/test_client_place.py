"""Browser-supplied location for weather turns (installed web app)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "zeev"))
import zeev


def test_client_place_rounds_and_names(monkeypatch):
    seen = []
    def fake(lat, lon):
        seen.append((lat, lon))
        return {"city": "Canton", "regionName": "Massachusetts"}
    monkeypatch.setattr(zeev, "_reverse_geocode", fake)
    zeev._CLIENT_PLACE_CACHE.clear()
    assert zeev.client_place({"lat": 42.16123, "lon": -71.14987}) == "Canton, Massachusetts"
    assert seen == [(42.16, -71.15)]  # ~1 km, never the raw fix


def test_client_place_rejects_garbage(monkeypatch):
    monkeypatch.setattr(zeev, "_reverse_geocode", lambda *a: (_ for _ in ()).throw(AssertionError("no geocode")))
    for bad in (None, {}, {"lat": "x", "lon": 1}, {"lat": 91, "lon": 0}, {"lat": 0, "lon": 181}, "42,-71"):
        assert zeev.client_place(bad) == ""


def test_weather_search_uses_client_place_over_ambient(monkeypatch):
    q = []
    monkeypatch.setattr(zeev, "TAVILY_API_KEY", "x")
    monkeypatch.setattr(zeev, "tavily_search", lambda query: q.append(query) or "results")
    monkeypatch.setattr(zeev, "ambient_place", lambda *a, **k: "Massachusetts, United States")
    monkeypatch.setattr(zeev, "client_place", lambda loc: "Canton, Massachusetts")
    monkeypatch.setattr(zeev, "open_meteo_weather", lambda *a: None)  # Tavily fallback path
    zeev._build_system_prompt("what's the weather today", session=[], client_loc={"lat": 42.1, "lon": -71.1})
    assert q and q[0].endswith("in Canton, Massachusetts")
    q.clear()
    monkeypatch.setattr(zeev, "_weather_coords", lambda c: None)
    zeev._build_system_prompt("what's the weather today", session=[])
    assert q[0].endswith("in Massachusetts, United States")


def test_web_ui_only_asks_location_for_weather():
    html = (pathlib.Path(zeev.__file__).parent / "web_ui.html").read_text()
    assert "navigator.geolocation" in html and "loc})" in html
    assert "weatherLoc(msg)" in html


# --- Open-Meteo weather -----------------------------------------------------

class _Resp:
    status_code = 200
    def __init__(self, body): self._b = body
    def json(self): return self._b

_OM = {
    "current": {"weather_code": 61, "temperature_2m": 55.4, "apparent_temperature": 52.0,
                "wind_speed_10m": 9.6, "relative_humidity_2m": 81},
    "daily": {"time": ["2026-09-25", "2026-09-26", "2026-09-27"], "weather_code": [61, 3, 0],
              "temperature_2m_max": [60, 66, 70], "temperature_2m_min": [48, 50, 52],
              "precipitation_probability_max": [80, None, 5]},
}


def test_open_meteo_formats_spoken_units(monkeypatch):
    monkeypatch.setattr(zeev.requests, "get", lambda *a, **k: _Resp(_OM))
    t = zeev.open_meteo_weather(42.16, -71.15)
    assert "light rain, 55 degrees Fahrenheit" in t and "miles per hour" in t
    assert "°" not in t and "mph" not in t
    assert "chance of precipitation unknown" in t  # None must not crash or read as 0


def test_open_meteo_failure_returns_none(monkeypatch):
    def boom(*a, **k): raise OSError("down")
    monkeypatch.setattr(zeev.requests, "get", boom)
    assert zeev.open_meteo_weather(1, 2) is None
    monkeypatch.setattr(zeev.requests, "get", lambda *a, **k: _Resp({"current": {}}))
    assert zeev.open_meteo_weather(1, 2) is None


def test_weather_turn_uses_open_meteo_and_skips_tavily(monkeypatch):
    q = []
    monkeypatch.setattr(zeev, "TAVILY_API_KEY", "x")
    monkeypatch.setattr(zeev, "tavily_search", lambda query: q.append(query) or "r")
    monkeypatch.setattr(zeev, "open_meteo_weather", lambda lat, lon: "Now: rain")
    monkeypatch.setattr(zeev, "client_place", lambda loc: "Canton, Massachusetts")
    p = zeev._build_system_prompt("what's the weather today", session=[], client_loc={"lat": 42.1, "lon": -71.1})
    assert "Now: rain" in p and "Canton, Massachusetts" in p
    assert q == []  # no Tavily credit spent


def test_weather_falls_back_to_tavily_when_open_meteo_fails(monkeypatch):
    q = []
    monkeypatch.setattr(zeev, "TAVILY_API_KEY", "x")
    monkeypatch.setattr(zeev, "tavily_search", lambda query: q.append(query) or "r")
    monkeypatch.setattr(zeev, "open_meteo_weather", lambda lat, lon: None)
    zeev._build_system_prompt("what's the weather today", session=[], client_loc={"lat": 42.1, "lon": -71.1})
    assert len(q) == 1


def test_weather_plus_news_still_searches(monkeypatch):
    q = []
    monkeypatch.setattr(zeev, "TAVILY_API_KEY", "x")
    monkeypatch.setattr(zeev, "tavily_search", lambda query: q.append(query) or "r")
    monkeypatch.setattr(zeev, "open_meteo_weather", lambda lat, lon: "Now: rain")
    zeev._build_system_prompt("what's the weather and the latest news", session=[], client_loc={"lat": 42.1, "lon": -71.1})
    assert len(q) == 1
