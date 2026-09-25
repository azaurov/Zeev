"""Critical NWS weather alerts announced on the device."""
import sys, pathlib, time, threading
from datetime import datetime, timedelta
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "zeev"))
import zeev

SRC = pathlib.Path(zeev.__file__).read_text()


def _alert(event, id_="urn:a1", **kw):
    ends = (datetime.now().astimezone() + timedelta(hours=3)).isoformat()
    base = {"id": id_, "event": event, "status": "Actual", "messageType": "Alert",
            "severity": "Severe", "areaDesc": "Norfolk; Suffolk; Bristol", "ends": ends,
            "headline": f"{event} issued"}
    base.update(kw)
    return base


def test_critical_events_announced():
    for ev in ("Tornado Warning", "Hurricane Warning", "Blizzard Warning", "Winter Storm Warning",
               "Tropical Storm Watch", "Coastal Flood Warning", "High Wind Warning"):
        assert zeev._is_critical_alert(_alert(ev)), ev


def test_routine_alerts_not_announced():
    # Canton had a live Wind Advisory while this was written -- must stay silent.
    for ev in ("Wind Advisory", "Frost Advisory", "Dense Fog Advisory", "Rip Current Statement",
               "Small Craft Advisory", "Special Weather Statement"):
        assert not zeev._is_critical_alert(_alert(ev, severity="Minor")), ev


def test_tests_and_cancellations_never_announced():
    assert not zeev._is_critical_alert(_alert("Tornado Warning", status="Test"))
    assert not zeev._is_critical_alert(_alert("Tornado Warning", messageType="Cancel"))


def test_noreaster_named_in_a_statement_counts():
    a = _alert("Special Weather Statement", severity="Moderate",
               headline="Nor'easter to bring heavy rain and wind")
    assert zeev._is_critical_alert(a)
    assert zeev._is_critical_alert(_alert("Special Weather Statement", severity="Moderate", headline="A Noreaster is coming"))


def test_extreme_severity_always_counts():
    assert zeev._is_critical_alert(_alert("Something Unusual", severity="Extreme"))


def test_dedup_announces_once_and_prunes_expired():
    ann = {}
    a = _alert("Tornado Warning")
    assert len(zeev.new_critical_alerts([a], ann)) == 1
    assert zeev.new_critical_alerts([a], ann) == []            # same id: silent
    assert len(zeev.new_critical_alerts([_alert("Tornado Warning", id_="urn:a2")], ann)) == 1
    ann["urn:old"] = time.time() - 10
    zeev.new_critical_alerts([], ann)
    assert "urn:old" not in ann                                # expired record pruned


def test_bad_expiry_falls_back_not_forever_or_instant():
    exp = zeev._alert_expiry_epoch(_alert("Tornado Warning", ends="garbage", expires=None))
    assert time.time() < exp < time.time() + 7 * 3600


def test_speech_is_spoken_words_and_capped():
    msg = zeev._alert_speech([_alert("Tornado Warning"), _alert("Flash Flood Warning", id_="b")])
    assert msg.startswith("Weather alert. Tornado Warning for Norfolk; Suffolk until ")
    assert "Bristol" not in msg                                # only first two areas
    many = zeev._alert_speech([_alert("Tornado Warning", id_=str(i)) for i in range(5)])
    assert "Plus 2 more." in many


def test_nws_failure_returns_none_and_404_means_none_active(monkeypatch):
    class R:
        def __init__(self, code): self.status_code = code
        def json(self): return {"features": []}
    monkeypatch.setattr(zeev.requests, "get", lambda *a, **k: R(500))
    assert zeev.nws_alerts(1, 2) is None
    monkeypatch.setattr(zeev.requests, "get", lambda *a, **k: R(404))
    assert zeev.nws_alerts(1, 2) == []
    def boom(*a, **k): raise OSError("down")
    monkeypatch.setattr(zeev.requests, "get", boom)
    assert zeev.nws_alerts(1, 2) is None


def test_loop_announces_once_across_polls_and_persists(monkeypatch, tmp_path):
    state = tmp_path / "ann.json"
    monkeypatch.setattr(zeev, "_WEATHER_ALERT_STATE", state)
    monkeypatch.setattr(zeev, "gps_cached", lambda: {"lat": 42.1, "lon": -71.1})
    monkeypatch.setattr(zeev, "nws_alerts", lambda lat, lon: [_alert("Tornado Warning"), _alert("Wind Advisory", id_="w", severity="Minor")])
    spoken = []

    class Stop(Exception): pass
    polls = {"n": 0}
    def fake_sleep(_):
        polls["n"] += 1
        if polls["n"] >= 3:
            raise SystemExit
    monkeypatch.setattr(zeev.time, "sleep", fake_sleep)
    try:
        zeev._weather_alert_loop(spoken.append)
    except SystemExit:
        pass
    assert len(spoken) == 1 and "Tornado Warning" in spoken[0] and "Wind Advisory" not in spoken[0]
    assert "urn:a1" in state.read_text()                       # survives a restart


def test_loop_wired_into_device_mode_after_notify_and_toggleable():
    i = SRC.index("_reminder_notify[0] = _announce_reminder")
    tail = SRC[i:i + 400]
    assert "_WEATHER_ALERTS_ON" in tail and "_weather_alert_loop" in tail


# --- Greater Boston / configured regions ------------------------------------

def test_parse_alert_regions_tolerates_junk():
    assert zeev.parse_alert_regions("MA:Suffolk, Norfolk;RI:Providence") == [
        ("MA", ["suffolk", "norfolk"]), ("RI", ["providence"])]
    assert zeev.parse_alert_regions("junk;;MA:;X:Foo;") == []
    assert zeev.parse_alert_regions(None) == []


def test_default_region_is_greater_boston():
    st, names = zeev.parse_alert_regions(zeev._ALERT_REGIONS)[0]
    assert st == "MA" and {"suffolk", "middlesex", "norfolk", "essex"} <= set(names)


def test_region_fetch_keeps_only_named_counties(monkeypatch):
    feats = [{"id": "u1", "properties": {"event": "High Wind Warning", "areaDesc": "Eastern Essex; Suffolk"}},
             {"id": "u2", "properties": {"event": "High Wind Warning", "areaDesc": "Berkshire; Hampshire"}}]
    class R:
        status_code = 200
        def json(self): return {"features": feats}
    monkeypatch.setattr(zeev.requests, "get", lambda *a, **k: R())
    got = zeev.nws_alerts_region("MA", ["suffolk"])
    assert [a["id"] for a in got] == ["u1"]


def test_region_failure_returns_none(monkeypatch):
    def boom(*a, **k): raise OSError("down")
    monkeypatch.setattr(zeev.requests, "get", boom)
    assert zeev.nws_alerts_region("MA", ["suffolk"]) is None


def test_gather_merges_point_and_region_and_dedups(monkeypatch):
    a, b = _alert("Tornado Warning", id_="same"), _alert("High Wind Warning", id_="only-region")
    monkeypatch.setattr(zeev, "nws_alerts", lambda lat, lon: [a])
    monkeypatch.setattr(zeev, "nws_alerts_region", lambda st, n: [a, b])
    got = zeev._gather_alerts((42.1, -71.1))
    assert sorted(x["id"] for x in got) == ["only-region", "same"]


def test_region_alerts_still_flow_when_device_has_no_fix(monkeypatch):
    b = _alert("High Wind Warning", id_="r")
    monkeypatch.setattr(zeev, "nws_alerts_region", lambda st, n: [b])
    assert [x["id"] for x in zeev._gather_alerts(None)] == ["r"]
