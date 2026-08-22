"""Zepp OS watch endpoint (zeev/watch_server.py).

Exercises the HTTP handler end-to-end (real socket, real threads) rather than
poking at internals, since the whole point of this module is the wire
contract a phone-side fetch() will actually hit: auth header, JSON shape,
status codes.
"""
import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "zeev"))


@pytest.fixture
def watch_server(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "ZEEV_WATCH_KEY", "test-secret")
    import watch_server as ws

    server = ThreadingHTTPServer(("127.0.0.1", 0), ws._make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield ws, server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _post(port, path, body=None, key="test-secret"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-Zeev-Watch-Key"] = key
    payload = json.dumps(body).encode() if body is not None else b""
    conn.request("POST", path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def test_missing_key_rejected(watch_server):
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "world_news"}, key=None)
    assert status == 403
    assert data["ok"] is False


def test_wrong_key_rejected(watch_server):
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "world_news"}, key="nope")
    assert status == 403


def test_no_configured_key_refuses_everything(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "ZEEV_WATCH_KEY", "")
    import watch_server as ws
    server = ThreadingHTTPServer(("127.0.0.1", 0), ws._make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _data = _post(server.server_address[1], "/watch",
                               {"cmd": "world_news"}, key="anything")
    finally:
        server.shutdown()
        thread.join(timeout=5)
    assert status == 403


def test_unknown_cmd_rejected(watch_server):
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "bogus"})
    assert status == 400
    assert data["ok"] is False


def test_malformed_json_rejected(watch_server):
    _ws, port = watch_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/watch", body=b"{not json",
                 headers={"X-Zeev-Watch-Key": "test-secret"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    assert resp.status == 400
    assert data["ok"] is False


def test_unknown_path_404(watch_server):
    _ws, port = watch_server
    status, _data = _post(port, "/nope", {"cmd": "world_news"})
    assert status == 404


def test_world_news_dispatches_get_shpeel(watch_server, zeev, monkeypatch):
    _ws, port = watch_server
    monkeypatch.setattr(zeev, "get_shpeel", lambda: "Today's shpeel: quiet everywhere.")
    status, data = _post(port, "/watch", {"cmd": "world_news"})
    assert status == 200
    assert data == {"ok": True, "message": "Today's shpeel: quiet everywhere."}


def test_world_news_speaks_through_audio_daemon_when_available(watch_server, zeev, monkeypatch):
    _ws, port = watch_server
    monkeypatch.setattr(zeev, "get_shpeel", lambda: "The shpeel.")
    spoken = []
    skip_espeak_flags = []
    fake_audio = type("FakeAudio", (), {
        "available": True,
        "speak": lambda self, text, **kw: (spoken.append(text),
                                            skip_espeak_flags.append(kw.get("skip_espeak"))),
    })()
    monkeypatch.setattr(zeev, "_audio", fake_audio)
    status, data = _post(port, "/watch", {"cmd": "world_news"})
    assert status == 200
    assert spoken == ["The shpeel."]
    # skip_espeak=True: a Kokoro/Piper failure here must stay silent, not
    # fall back to the robotic espeak-ng voice.
    assert skip_espeak_flags == [True]


def test_world_news_speak_failure_does_not_break_response(watch_server, zeev, monkeypatch):
    _ws, port = watch_server
    monkeypatch.setattr(zeev, "get_shpeel", lambda: "The shpeel.")
    fake_audio = type("FakeAudio", (), {
        "available": True,
        "speak": lambda self, text, **kw: (_ for _ in ()).throw(RuntimeError("daemon gone")),
    })()
    monkeypatch.setattr(zeev, "_audio", fake_audio)
    status, data = _post(port, "/watch", {"cmd": "world_news"})
    assert status == 200
    assert data == {"ok": True, "message": "The shpeel."}


def test_world_news_no_speak_when_audio_unavailable(watch_server, zeev, monkeypatch):
    _ws, port = watch_server
    monkeypatch.setattr(zeev, "get_shpeel", lambda: "The shpeel.")
    monkeypatch.setattr(zeev, "_audio", None)
    status, data = _post(port, "/watch", {"cmd": "world_news"})
    assert status == 200
    assert data == {"ok": True, "message": "The shpeel."}


def test_pair_ble_success(watch_server, zeev, monkeypatch):
    ws, port = watch_server
    monkeypatch.setattr(zeev, "bt_list", lambda: [])
    monkeypatch.setattr(zeev, "bt_pair", lambda mac: mac == ws._BLE_TARGET_MAC)
    monkeypatch.setattr(zeev, "bt_connect", lambda mac: mac == ws._BLE_TARGET_MAC)
    status, data = _post(port, "/watch", {"cmd": "pair_ble"})
    assert status == 200
    assert data["ok"] is True
    assert ws._BLE_TARGET_NAME in data["message"]


def test_pair_ble_already_connected_skips_reconnect(watch_server, zeev, monkeypatch):
    # BlueZ returns a spurious page-timeout error when asked to connect an
    # already-connected device -- bt_pair/bt_connect must not even be called.
    ws, port = watch_server
    monkeypatch.setattr(zeev, "bt_list", lambda: [(ws._BLE_TARGET_MAC, "TOZO NC9", True)])
    monkeypatch.setattr(zeev, "bt_pair", lambda mac: (_ for _ in ()).throw(AssertionError("should not be called")))
    monkeypatch.setattr(zeev, "bt_connect", lambda mac: (_ for _ in ()).throw(AssertionError("should not be called")))
    status, data = _post(port, "/watch", {"cmd": "pair_ble"})
    assert status == 200
    assert data["ok"] is True
    assert "Already connected" in data["message"]


def test_pair_ble_pair_failure(watch_server, zeev, monkeypatch):
    monkeypatch.setattr(zeev, "bt_list", lambda: [])
    monkeypatch.setattr(zeev, "bt_pair", lambda mac: False)
    monkeypatch.setattr(zeev, "bt_connect", lambda mac: True)
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "pair_ble"})
    assert status == 200
    assert data["ok"] is False


def test_pair_ble_connect_failure(watch_server, zeev, monkeypatch):
    monkeypatch.setattr(zeev, "bt_list", lambda: [])
    monkeypatch.setattr(zeev, "bt_pair", lambda mac: True)
    monkeypatch.setattr(zeev, "bt_connect", lambda mac: False)
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "pair_ble"})
    assert status == 200
    assert data["ok"] is False


def test_find_smokey_dispatches_sweep(watch_server, zeev, monkeypatch):
    subj = {"name": "Smokey", "kind": "cat", "cams": ["basement-cam"]}
    monkeypatch.setattr(zeev, "resolve_subject", lambda text: subj)
    monkeypatch.setattr(zeev, "sweep_for_subject",
                         lambda s, **kw: ("Smokey is on the basement cam.", 1))
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "find_smokey"})
    assert status == 200
    assert data == {"ok": True, "message": "Smokey is on the basement cam."}


def test_find_smokey_speaks_through_audio_daemon_when_available(watch_server, zeev, monkeypatch):
    subj = {"name": "Smokey", "kind": "cat", "cams": ["basement-cam"]}
    monkeypatch.setattr(zeev, "resolve_subject", lambda text: subj)
    monkeypatch.setattr(zeev, "sweep_for_subject",
                         lambda s, **kw: ("Smokey is on the basement cam.", 1))
    spoken = []
    skip_espeak_flags = []
    fake_audio = type("FakeAudio", (), {
        "available": True,
        "speak": lambda self, text, **kw: (spoken.append(text),
                                            skip_espeak_flags.append(kw.get("skip_espeak"))),
    })()
    monkeypatch.setattr(zeev, "_audio", fake_audio)
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "find_smokey"})
    assert status == 200
    assert spoken == ["Smokey is on the basement cam."]
    assert skip_espeak_flags == [True]


def test_find_smokey_unconfigured(watch_server, zeev, monkeypatch):
    monkeypatch.setattr(zeev, "resolve_subject", lambda text: None)
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "find_smokey"})
    assert status == 200
    assert data["ok"] is False


def test_blessing_netilas_yadayim_dispatches_torah_search(watch_server, zeev, monkeypatch):
    queries = []

    def fake_search(query, k=3):
        queries.append((query, k))
        return [("Siddur Ashkenaz, Weekday, Shacharit, Preparatory Prayers, Netilat Yadayim",
                  "Blessed1 are You, Adonoy2 our God, King of the Universe, "
                  "Who sanctified us with His commandments and commanded us "
                  "concerning the washing of hands.",
                  "he text")]

    monkeypatch.setattr(zeev, "torah_search", fake_search)
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "blessing_netilas_yadayim"})
    assert status == 200
    assert data["ok"] is True
    assert "washing of hands" in data["message"]
    # Sefaria footnote-reference digits ("Blessed1", "Adonoy2") must not reach
    # the response -- they'd be spoken aloud as "one"/"two" with no LLM in
    # between to smooth them out.
    assert "1" not in data["message"] and "2" not in data["message"]
    assert "Blessed are You" in data["message"]
    # Canonical DB spelling ("Netilat"), not the Ashkenazi "Netilas" a person
    # would say -- this is a hardcoded button tap, not free text, and
    # _torah_ref_lookup's LIKE probe needs the literal spelling in `ref`.
    assert queries == [("Netilat Yadayim", 1)]


def test_strip_footnote_markers():
    import watch_server as ws
    assert ws._strip_footnote_markers("Blessed1 are You, Adonoy2 our God.") == \
        "Blessed are You, Adonoy our God."
    # Space-separated numbers (verse/chapter counts) must survive untouched.
    assert ws._strip_footnote_markers("recited 3 times on day 40") == \
        "recited 3 times on day 40"


def test_blessing_speaks_hebrew_slow_when_he_text_available(watch_server, zeev, monkeypatch):
    # Alex wants to hear the actual pronunciation, not a fast English
    # reading -- Hebrew (when the DB has it) goes through the gTTS+ffmpeg
    # slow path, not the Go daemon's English-only speak().
    ws, port = watch_server
    monkeypatch.setattr(zeev, "torah_search",
                         lambda query, k=3: [("ref", "The blessing text.", "בָּרוּךְ אַתָּה")])
    spoken_he = []
    monkeypatch.setattr(ws, "_speak_hebrew_slow", lambda text, **kw: spoken_he.append(text))
    daemon_spoken = []
    monkeypatch.setattr(ws, "_speak", lambda text: daemon_spoken.append(text))
    status, data = _post(port, "/watch", {"cmd": "blessing_netilas_yadayim"})
    assert status == 200
    assert spoken_he == ["בָּרוּךְ אַתָּה"]
    assert daemon_spoken == []
    # Watch screen still shows the English text -- Zepp OS bitmap fonts
    # aren't guaranteed to render Hebrew glyphs.
    assert data["message"] == "The blessing text."


def test_blessing_falls_back_to_english_speech_when_no_he_text(watch_server, zeev, monkeypatch):
    ws, port = watch_server
    monkeypatch.setattr(zeev, "torah_search",
                         lambda query, k=3: [("ref", "The blessing text.", "")])
    spoken_he = []
    monkeypatch.setattr(ws, "_speak_hebrew_slow", lambda text, **kw: spoken_he.append(text))
    daemon_spoken = []
    monkeypatch.setattr(ws, "_speak", lambda text: daemon_spoken.append(text))
    status, data = _post(port, "/watch", {"cmd": "blessing_netilas_yadayim"})
    assert status == 200
    assert spoken_he == []
    assert daemon_spoken == ["The blessing text."]


def test_blessing_not_found_in_db(watch_server, zeev, monkeypatch):
    monkeypatch.setattr(zeev, "torah_search", lambda query, k=3: [])
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "blessing_netilas_yadayim"})
    assert status == 200
    assert data["ok"] is False


def test_blessing_empty_english_text(watch_server, zeev, monkeypatch):
    monkeypatch.setattr(zeev, "torah_search", lambda query, k=3: [("ref", "", "he")])
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "blessing_netilas_yadayim"})
    assert status == 200
    assert data["ok"] is False


class _SyncThread:
    """threading.Thread stand-in that runs its target immediately and
    synchronously, so a test can assert on _speak_hebrew_slow's subprocess
    calls without racing a real background thread."""
    def __init__(self, target, daemon=None):
        self._target = target

    def start(self):
        self._target()


def test_speak_hebrew_slow_pipeline(zeev, monkeypatch):
    import watch_server as ws

    monkeypatch.setattr(ws.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(ws, "threading", type("T", (), {"Thread": _SyncThread}))
    monkeypatch.setattr(ws, "_current_audio_dev", lambda: "bluealsa:DEV=AA:BB,PROFILE=a2dp")
    monkeypatch.setattr(zeev, "_gtts_chunks", lambda text: ["chunk one"])
    monkeypatch.setattr(zeev, "_gtts_fetch_chunk", lambda chunk, lang: (b"mp3bytes" if lang == "he" else None))

    calls = []

    class FakeProc:
        def __init__(self, cmd, **kw):
            calls.append(cmd)
            self._is_ffmpeg = cmd[0] == "ffmpeg"

        def communicate(self, input=None, timeout=None):
            if self._is_ffmpeg:
                return (b"slowed-mp3", b"")
            return (b"", b"")

    monkeypatch.setattr(ws.subprocess, "Popen", FakeProc)

    ws._speak_hebrew_slow("בָּרוּךְ אַתָּה")

    assert len(calls) == 2
    ffmpeg_cmd, mpg123_cmd = calls
    assert ffmpeg_cmd[0] == "ffmpeg"
    assert "atempo=0.5" in ffmpeg_cmd
    assert mpg123_cmd[0] == "mpg123"
    assert "bluealsa:DEV=AA:BB,PROFILE=a2dp" in mpg123_cmd


def test_command_exception_returns_500(watch_server, zeev, monkeypatch):
    def _boom():
        raise RuntimeError("network is down")
    monkeypatch.setattr(zeev, "get_shpeel", _boom)
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "world_news"})
    assert status == 500
    assert data["ok"] is False
