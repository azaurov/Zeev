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


def test_find_smokey_dispatches_sweep_for_both_leo_and_smokey(watch_server, zeev, monkeypatch):
    leo = {"name": "Leo", "kind": "dog", "cams": ["basement-cam"]}
    smokey = {"name": "Smokey", "kind": "cat", "cams": ["basement-cam"]}
    monkeypatch.setattr(zeev, "WYZE_SUBJECTS", {"leo": leo, "smokey": smokey})
    swept = []

    def fake_sweep(subj, **kw):
        swept.append(subj["name"])
        return f"{subj['name']} is on the basement cam.", 1

    monkeypatch.setattr(zeev, "sweep_for_subject", fake_sweep)
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "find_smokey"})
    assert status == 200
    assert data["ok"] is True
    # Leo swept before Smokey (order of _FIND_SUBJECT_KEYS).
    assert swept == ["Leo", "Smokey"]
    assert "Leo is on the basement cam." in data["message"]
    assert "Smokey is on the basement cam." in data["message"]


def test_find_smokey_speaks_through_audio_daemon_when_available(watch_server, zeev, monkeypatch):
    leo = {"name": "Leo", "kind": "dog", "cams": ["basement-cam"]}
    smokey = {"name": "Smokey", "kind": "cat", "cams": ["basement-cam"]}
    monkeypatch.setattr(zeev, "WYZE_SUBJECTS", {"leo": leo, "smokey": smokey})
    monkeypatch.setattr(zeev, "sweep_for_subject",
                         lambda s, **kw: (f"{s['name']} is on the basement cam.", 1))
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
    assert spoken == ["Leo is on the basement cam. Smokey is on the basement cam."]
    assert skip_espeak_flags == [True]


def test_find_smokey_partially_configured(watch_server, zeev, monkeypatch):
    # Leo missing from ZEEV_SUBJECTS, Smokey present -- still a successful
    # sweep (of just Smokey), not a total failure.
    smokey = {"name": "Smokey", "kind": "cat", "cams": ["basement-cam"]}
    monkeypatch.setattr(zeev, "WYZE_SUBJECTS", {"smokey": smokey})
    monkeypatch.setattr(zeev, "sweep_for_subject",
                         lambda s, **kw: ("Smokey is on the basement cam.", 1))
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "find_smokey"})
    assert status == 200
    assert data["ok"] is True
    assert "Leo isn't configured" in data["message"]
    assert "Smokey is on the basement cam." in data["message"]


def test_find_smokey_unconfigured(watch_server, zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_SUBJECTS", {})
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


def test_substitute_tetragrammaton():
    import watch_server as ws
    # Live 2026-08-22: Google TTS didn't pronounce the divine name at all --
    # it's not a word in its Hebrew training data, since tradition never
    # reads it as written. Substituting "Adonai" (how it's actually read
    # aloud in prayer) before synthesis is both religiously correct and the
    # actual fix.
    text = "בָּרוּךְ אַתָּה יְהֹוָה אֱלֹהֵֽינוּ מֶֽלֶךְ הָעוֹלָם"
    result = ws._substitute_tetragrammaton(text)
    assert "יְהֹוָה" not in result
    assert ws._ADONAI in result
    # A plain, unvowelized string with no Tetragrammaton must pass through
    # untouched.
    assert ws._substitute_tetragrammaton("שלום עליכם") == "שלום עליכם"


def test_strip_niqud():
    import watch_server as ws
    # Live 2026-08-22: Google's Hebrew TTS mispronounced "Eloheinu" from the
    # DB's heavily vowel-pointed text. Stripped niqud must equal the standard
    # unvocalized printed form of the same words.
    text = ("בָּרוּךְ אַתָּה אֲדֹנָי אֱלֹהֵֽינוּ מֶֽלֶךְ הָעוֹלָם אֲשֶׁר קִדְּ֒שָֽׁנוּ "
            "בְּמִצְוֹתָיו וְצִוָּנוּ עַל נְטִילַת יָדָֽיִם:")
    assert ws._strip_niqud(text) == \
        "ברוך אתה אדני אלהינו מלך העולם אשר קדשנו במצותיו וצונו על נטילת ידים:"
    # No niqud present must pass through untouched.
    assert ws._strip_niqud("שלום עליכם") == "שלום עליכם"


def test_blessing_speaks_hebrew_when_he_text_available(watch_server, zeev, monkeypatch):
    # Alex wants to hear the actual pronunciation, not a fast English
    # reading -- Hebrew (when the DB has it) goes through _speak_hebrew
    # (Cartesia-first, gTTS-fallback), not the Go daemon's English-only
    # speak().
    ws, port = watch_server
    monkeypatch.setattr(zeev, "torah_search",
                         lambda query, k=3: [("ref", "The blessing text.", "בָּרוּךְ אַתָּה")])
    calls = []
    monkeypatch.setattr(ws, "_speak_hebrew",
                         lambda niqud, no_niqud, **kw: calls.append((niqud, no_niqud)))
    daemon_spoken = []
    monkeypatch.setattr(ws, "_speak", lambda text: daemon_spoken.append(text))
    status, data = _post(port, "/watch", {"cmd": "blessing_netilas_yadayim"})
    assert status == 200
    # Full niqud text goes to Cartesia (a real phonetic model should use the
    # vowel points); the niqud-stripped text is the gTTS fallback's input
    # (see test_strip_niqud -- gTTS mishandles heavily vowel-pointed text).
    assert calls == [("בָּרוּךְ אַתָּה", "ברוך אתה")]
    assert daemon_spoken == []
    # Watch screen still shows the English text -- Zepp OS bitmap fonts
    # aren't guaranteed to render Hebrew glyphs.
    assert data["message"] == "The blessing text."


def test_blessing_falls_back_to_english_speech_when_no_he_text(watch_server, zeev, monkeypatch):
    ws, port = watch_server
    monkeypatch.setattr(zeev, "torah_search",
                         lambda query, k=3: [("ref", "The blessing text.", "")])
    calls = []
    monkeypatch.setattr(ws, "_speak_hebrew", lambda niqud, no_niqud, **kw: calls.append(niqud))
    daemon_spoken = []
    monkeypatch.setattr(ws, "_speak", lambda text: daemon_spoken.append(text))
    status, data = _post(port, "/watch", {"cmd": "blessing_netilas_yadayim"})
    assert status == 200
    assert calls == []
    assert daemon_spoken == ["The blessing text."]


def test_cartesia_tts_hebrew_no_key_returns_none(zeev, monkeypatch):
    import watch_server as ws
    monkeypatch.setattr(zeev, "CARTESIA_API_KEY", "")
    assert ws._cartesia_tts_hebrew("שלום") is None


def test_cartesia_tts_hebrew_sends_language_and_full_niqud_text(zeev, monkeypatch):
    import watch_server as ws
    monkeypatch.setattr(zeev, "CARTESIA_API_KEY", "test-key")
    monkeypatch.setattr(zeev, "CARTESIA_VOICE_ID", "some-voice-id")

    posted = {}

    class FakeResp:
        status_code = 200
        content = b"RIFF...wav-bytes"

    def fake_post(url, headers=None, json=None, timeout=None):
        posted["url"] = url
        posted["headers"] = headers
        posted["json"] = json
        return FakeResp()

    monkeypatch.setattr(ws.requests, "post", fake_post)
    result = ws._cartesia_tts_hebrew("בָּרוּךְ אַתָּה")
    assert result == b"RIFF...wav-bytes"
    assert posted["json"]["model_id"] == "sonic-3.5"
    assert posted["json"]["language"] == "he"
    # Full niqud text, not stripped -- a real phonetic model should use it.
    assert posted["json"]["transcript"] == "בָּרוּךְ אַתָּה"
    assert posted["json"]["voice"] == {"mode": "id", "id": "some-voice-id"}
    assert posted["headers"]["X-API-Key"] == "test-key"


def test_cartesia_tts_hebrew_non_200_returns_none(zeev, monkeypatch):
    import watch_server as ws
    monkeypatch.setattr(zeev, "CARTESIA_API_KEY", "test-key")

    class FakeResp:
        status_code = 402
        text = "payment required"

    monkeypatch.setattr(ws.requests, "post", lambda *a, **kw: FakeResp())
    assert ws._cartesia_tts_hebrew("שלום") is None


def test_cartesia_tts_hebrew_exception_returns_none(zeev, monkeypatch):
    import watch_server as ws
    monkeypatch.setattr(zeev, "CARTESIA_API_KEY", "test-key")

    def raise_it(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(ws.requests, "post", raise_it)
    assert ws._cartesia_tts_hebrew("שלום") is None


def test_speak_hebrew_uses_cartesia_when_available(zeev, monkeypatch):
    import watch_server as ws
    monkeypatch.setattr(ws, "threading", type("T", (), {"Thread": _SyncThread}))
    monkeypatch.setattr(ws, "_cartesia_tts_hebrew", lambda text: b"wav-bytes")
    monkeypatch.setattr(ws.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(ws, "_current_audio_dev", lambda: "default")

    calls = []
    fallback_calls = []
    monkeypatch.setattr(ws, "_speak_hebrew_slow", lambda text, **kw: fallback_calls.append(text))

    class FakeProc:
        def __init__(self, cmd, **kw):
            calls.append(cmd)
            self._is_ffmpeg = cmd[0] == "ffmpeg"

        def communicate(self, input=None, timeout=None):
            return (b"slowed-mp3", b"") if self._is_ffmpeg else (b"", b"")

    monkeypatch.setattr(ws.subprocess, "Popen", FakeProc)

    ws._speak_hebrew("בָּרוּךְ אַתָּה", "ברוך אתה")

    assert fallback_calls == []
    assert len(calls) == 2
    assert calls[0][0] == "ffmpeg"
    assert calls[1][0] == "mpg123"


def test_speak_hebrew_falls_back_to_gtts_when_cartesia_unavailable(zeev, monkeypatch):
    import watch_server as ws
    monkeypatch.setattr(ws, "threading", type("T", (), {"Thread": _SyncThread}))
    monkeypatch.setattr(ws, "_cartesia_tts_hebrew", lambda text: None)

    fallback_calls = []
    monkeypatch.setattr(ws, "_speak_hebrew_slow", lambda text, **kw: fallback_calls.append(text))

    ws._speak_hebrew("בָּרוּךְ אַתָּה", "ברוך אתה")

    assert fallback_calls == ["ברוך אתה"]


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


def test_all_ten_blessing_buttons_registered(watch_server):
    ws, _port = watch_server
    expected_keys = {
        "netilas_yadayim", "hamotzi", "mezonos", "hagofen", "hoaytz",
        "hoadomo", "shehakol", "brich", "modeh_ani", "shema",
    }
    assert {e["key"] for e in ws._BLESSINGS} == expected_keys
    for key in expected_keys:
        assert f"blessing_{key}" in ws._COMMANDS


def test_literal_blessing_dispatches_hardcoded_text(watch_server, zeev, monkeypatch):
    # Hamotzi has no clean torah.db entry (found live 2026-08-22) -- it must
    # NOT touch zeev.torah_search at all, only speak/return the hardcoded
    # text.
    ws, port = watch_server
    monkeypatch.setattr(zeev, "torah_search",
                         lambda query, k=3: (_ for _ in ()).throw(AssertionError("should not be called")))
    calls = []
    monkeypatch.setattr(ws, "_speak_hebrew", lambda niqud, no_niqud, **kw: calls.append((niqud, no_niqud)))
    status, data = _post(port, "/watch", {"cmd": "blessing_hamotzi"})
    assert status == 200
    assert data["ok"] is True
    assert "bread from the earth" in data["message"]
    assert len(calls) == 1
    assert "אֲדֹנָי" in calls[0][0]  # Tetragrammaton already substituted
    assert "יְהֹוָה" not in calls[0][0]


def test_ref_blessing_uses_exact_ref_not_fuzzy_search(watch_server, zeev, monkeypatch):
    # Live 2026-08-22: a fuzzy "Shema" query resolved to the wrong passage
    # (Talmud Berakhot 2a) -- Modeh Ani must go through the exact-ref lookup,
    # never zeev.torah_search.
    ws, port = watch_server
    monkeypatch.setattr(zeev, "torah_search",
                         lambda query, k=3: (_ for _ in ()).throw(AssertionError("should not be called")))
    monkeypatch.setattr(ws, "_torah_by_exact_ref",
                         lambda ref: (ref, "I give thanks...", "מוֹדֶה אֲנִי"))
    status, data = _post(port, "/watch", {"cmd": "blessing_modeh_ani"})
    assert status == 200
    assert data == {"ok": True, "message": "I give thanks..."}


def test_ref_blessing_not_found(watch_server, zeev, monkeypatch):
    ws, port = watch_server
    monkeypatch.setattr(ws, "_torah_by_exact_ref", lambda ref: None)
    status, data = _post(port, "/watch", {"cmd": "blessing_modeh_ani"})
    assert status == 200
    assert data["ok"] is False


def test_torah_by_exact_ref_live_db():
    import watch_server as ws
    row = ws._torah_by_exact_ref(
        "Siddur Ashkenaz, Weekday, Shacharit, Preparatory Prayers, Modeh Ani")
    assert row is not None
    ref, en, he = row
    assert "thank" in en.lower()
    assert he


def test_shema_is_hardcoded_not_db_sourced(watch_server, zeev, monkeypatch):
    # torah.db's Shema entry has footnote commentary interleaved into the
    # prayer text itself (found live 2026-08-22) -- Alex chose to hardcode
    # just the core first line instead. Must not touch the DB at all.
    ws, port = watch_server
    monkeypatch.setattr(zeev, "torah_search",
                         lambda query, k=3: (_ for _ in ()).throw(AssertionError("should not be called")))
    monkeypatch.setattr(ws, "_torah_by_exact_ref",
                         lambda ref: (_ for _ in ()).throw(AssertionError("should not be called")))
    status, data = _post(port, "/watch", {"cmd": "blessing_shema"})
    assert status == 200
    assert data == {"ok": True, "message": "Hear, O Israel: Adonoy is our God, Adonoy is One."}


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
    af = ffmpeg_cmd[ffmpeg_cmd.index("-af") + 1]
    assert "atempo=0.6" in af
    assert "volume=1.15" in af
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
