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
    zeev._build_system_prompt("what's the weather today", session=[], client_loc={"lat": 42.1, "lon": -71.1})
    assert q and q[0].endswith("in Canton, Massachusetts")
    q.clear()
    zeev._build_system_prompt("what's the weather today", session=[])
    assert q[0].endswith("in Massachusetts, United States")


def test_web_ui_only_asks_location_for_weather():
    html = (pathlib.Path(zeev.__file__).parent / "web_ui.html").read_text()
    assert "navigator.geolocation" in html and "loc})" in html
    assert "weatherLoc(msg)" in html
