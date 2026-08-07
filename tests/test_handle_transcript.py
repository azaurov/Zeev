"""Branch routing through handle_transcript().

This is the payoff of hoisting the handler out of run_device_mode: the intent
router — ~19 branches, the largest single piece of device-mode logic — can now
be driven with no Whisplay HAT, no audio daemon and no database.

These were written only after the branches were verified by voice on the Pi, so
they pin intended behaviour rather than whatever the port happened to produce.
"""
import re
import threading
import types

import pytest


@pytest.fixture
def ctx(zeev, monkeypatch):
    """A device context wired to spies instead of hardware."""
    monkeypatch.setattr(zeev, "append_message", lambda *a, **k: None)
    monkeypatch.setattr(zeev, "_audio", None)

    # _DeviceCtx defines __slots__, so it cannot carry the spy lists. A
    # subclass without its own __slots__ gains a __dict__ — keeps the
    # production class lean instead of widening it for tests.
    class _TestCtx(zeev._DeviceCtx):
        pass

    c = _TestCtx()
    c.session = []
    c.spoke, c.faces, c.states = [], [], []
    c.board = types.SimpleNamespace(set_rgb=lambda *a: None)
    c._set_face = lambda state, caption="": c.faces.append((state, caption))
    c._go_idle = lambda: c.states.append("idle")
    c._go_ready = lambda: c.states.append("ready")
    c._speak_device = lambda text, voice="sarina": c.spoke.append(text)
    c._progressive_speak = lambda text, voice="sarina": c.spoke.append(text)
    c._stream_speak = lambda *a, **k: ""
    c._busy = threading.Event()
    c._speak_cancel = threading.Event()
    c._pending_detail = [None]
    c._pending_detail_ready = threading.Event()
    c._pending_detail_source = [None]
    c._turn_count = [0]
    c._voice_coach_pending = [False]
    c._visual_effect_active = [False]
    c._LED_ERROR = c._LED_SPEAKING = c._LED_THINKING = (0, 0, 0)
    c._MORE_YES_RE = re.compile(
        r"\b(yes|yeah|yep|sure|go ahead|please|tell me|more|continue|expand)\b", re.I)
    c._have_pil = False
    c._followup_listen = lambda: ""
    return c


def _no_llm(monkeypatch, zeev):
    """Fail loudly if a branch falls through to the LLM when it shouldn't."""
    def boom(*a, **k):
        raise AssertionError("fell through to the LLM")
    monkeypatch.setattr(zeev, "_groq_post_with_fallback", boom)
    monkeypatch.setattr(zeev, "_groq_post", boom)


# ---------------------------------------------------------------------------
# Branches that must NOT reach the LLM
# ---------------------------------------------------------------------------

def test_music_stop_handled_locally(zeev, ctx, monkeypatch):
    _no_llm(monkeypatch, zeev)
    zeev.handle_transcript(ctx, "stop the music")
    assert ctx.spoke and "playing" in ctx.spoke[-1].lower()


def test_music_play_handled_locally(zeev, ctx, monkeypatch):
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "youtube_play", lambda q, adev=None: ("t", None))
    zeev.handle_transcript(ctx, "play some Miles Davis")
    assert "Looking for" in ctx.spoke[-1]


def test_language_switch_handled_locally(zeev, ctx, monkeypatch):
    _no_llm(monkeypatch, zeev)
    try:
        zeev.handle_transcript(ctx, "speak Russian")
        assert zeev.FORCED_LANG == "ru"
    finally:
        zeev.FORCED_LANG = None


def test_language_switch_back_to_english_clears_forced(zeev, ctx, monkeypatch):
    _no_llm(monkeypatch, zeev)
    zeev.FORCED_LANG = "ru"
    try:
        zeev.handle_transcript(ctx, "speak English")
        assert zeev.FORCED_LANG is None
    finally:
        zeev.FORCED_LANG = None


# ---------------------------------------------------------------------------
# Every branch must record the exchange and return the device to a rest state
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "stop the music",
    "speak Russian",
])
def test_branches_record_and_settle(zeev, ctx, monkeypatch, text):
    _no_llm(monkeypatch, zeev)
    try:
        zeev.handle_transcript(ctx, text)
    finally:
        zeev.FORCED_LANG = None
    roles = [m["role"] for m in ctx.session]
    assert roles == ["user", "assistant"], roles
    assert ctx.states and ctx.states[-1] in ("idle", "ready")


def test_session_is_shared_not_rebound_away(zeev, ctx, monkeypatch):
    """The hazard the ctx design exists for: the handler rebinds ctx.session on
    truncation while finish_turn appends to it. If they ever referenced
    different lists, history would silently stop growing."""
    _no_llm(monkeypatch, zeev)
    for _ in range(3):
        zeev.handle_transcript(ctx, "stop the music")
    assert len(ctx.session) == 6


# ---------------------------------------------------------------------------
# Pending detail ("Want to hear more?")
# ---------------------------------------------------------------------------

def test_yes_delivers_pending_detail(zeev, ctx, monkeypatch):
    _no_llm(monkeypatch, zeev)
    ctx._pending_detail[0] = "THE LONGER ANSWER"
    ctx._pending_detail_source[0] = "tell me about the Talmud"
    ctx._pending_detail_ready.set()
    zeev.handle_transcript(ctx, "yes")
    assert ctx.spoke[-1] == "THE LONGER ANSWER"
    assert ctx._pending_detail_source[0] is None, "pending state not cleared"


def test_unrelated_input_clears_pending_detail(zeev, ctx, monkeypatch):
    """Otherwise the offer stays armed and a much later 'yes' replays a stale
    answer to a question the user has long since moved on from."""
    _no_llm(monkeypatch, zeev)
    ctx._pending_detail[0] = "STALE"
    ctx._pending_detail_source[0] = "old topic"
    ctx._pending_detail_ready.set()
    zeev.handle_transcript(ctx, "stop the music")
    assert ctx._pending_detail_source[0] is None


# ---------------------------------------------------------------------------
# Wake-word voice routing
# ---------------------------------------------------------------------------

def test_wake_voice_overrides_transcript_inference(zeev, ctx, monkeypatch):
    """A wake word states who was addressed; the transcript regex only guesses."""
    _no_llm(monkeypatch, zeev)
    spoken_voice = []
    ctx._speak_device = lambda text, voice="sarina": spoken_voice.append(voice)
    zeev._WAKE_VOICE[0] = "daniel"
    try:
        # A branch that returns early — not the LLM path. Voice routing has to
        # apply here too, or "Hey Zeev, stop the music" answers as Sarina.
        zeev.handle_transcript(ctx, "stop the music")
        assert spoken_voice == ["daniel"], spoken_voice
        assert zeev._WAKE_VOICE[0] is None, "wake voice must be consumed once"
    finally:
        zeev._WAKE_VOICE[0] = None


def test_without_wake_voice_falls_back_to_inference(zeev, ctx, monkeypatch):
    """Button-press turns have no wake word, so the transcript decides."""
    _no_llm(monkeypatch, zeev)
    spoken_voice = []
    ctx._speak_device = lambda text, voice="sarina": spoken_voice.append(voice)
    zeev._WAKE_VOICE[0] = None
    zeev.handle_transcript(ctx, "stop the music")
    assert spoken_voice == ["sarina"], spoken_voice


def test_wake_voice_does_not_leak_into_the_next_turn(zeev, ctx, monkeypatch):
    """It is consumed once. Otherwise one "Hey Zeev" makes every later turn
    answer as Daniel until something else overwrites it."""
    _no_llm(monkeypatch, zeev)
    spoken_voice = []
    ctx._speak_device = lambda text, voice="sarina": spoken_voice.append(voice)
    zeev._WAKE_VOICE[0] = "daniel"
    try:
        zeev.handle_transcript(ctx, "stop the music")
        zeev.handle_transcript(ctx, "stop the music")
        assert spoken_voice == ["daniel", "sarina"], spoken_voice
    finally:
        zeev._WAKE_VOICE[0] = None


def test_voice_map_parsing(zeev):
    """OWW_VOICE_MAP is parsed at import; malformed entries must not explode."""
    assert isinstance(zeev.OWW_VOICE_MAP, dict)


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("junk", ["", "   ", "!!!", "A"])
def test_junk_transcripts_do_not_raise(zeev, ctx, monkeypatch, junk):
    """STT hands these over; a raise here kills the turn thread silently."""
    monkeypatch.setattr(zeev, "_groq_post_with_fallback",
                        lambda *a, **k: (None, "offline"))
    monkeypatch.setattr(zeev, "_groq_post", lambda *a, **k: (None, "offline"))
    zeev.handle_transcript(ctx, junk)


def test_llm_failure_surfaces_an_error_state(zeev, ctx, monkeypatch):
    monkeypatch.setattr(zeev, "_groq_post_with_fallback",
                        lambda *a, **k: (None, "connection refused"))
    monkeypatch.setattr(zeev, "_groq_post", lambda *a, **k: (None, "connection refused"))
    zeev.handle_transcript(ctx, "how are you today")
    assert any(f[0] == "error" for f in ctx.faces), ctx.faces
    assert ctx.states[-1] in ("idle", "ready")


# --- Wyze camera branch -----------------------------------------------------
#
# The gate used to require WYZE_RTSP_BASE as well as WYZE_CAMERAS, which
# silently disabled the entire branch the moment a camera moved to its own
# per-camera URL and the bridge base was no longer set. Live result: "what's
# going on upstairs" fell through to the LLM, which answered that it has no
# access to the upstairs -- confident, fluent, and completely wrong. Whether a
# camera is reachable belongs to wyze_stream_url(), not to the gate.

def _wyze_env(zeev, monkeypatch, base="", urls=None):
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", ["upstairs", "basement-cam"])
    monkeypatch.setattr(zeev, "WYZE_RTSP_BASE", base)
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS",
                        urls if urls is not None else {"upstairs": "rtsp://10.0.0.217:554/stream0"})


def test_camera_branch_fires_with_no_bridge_base(zeev, ctx, monkeypatch):
    """The exact live regression: per-camera URL only, no WYZE_RTSP_BASE."""
    _wyze_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: "ZmFrZQ==")
    monkeypatch.setattr(zeev, "vision_complete", lambda *a, **k: ("A quiet hallway.", None))
    zeev.handle_transcript(ctx, "what's going on upstairs")
    assert any("quiet hallway" in s.lower() for s in ctx.spoke), ctx.spoke


def test_camera_branch_still_fires_via_bridge_base(zeev, ctx, monkeypatch):
    _wyze_env(zeev, monkeypatch, base="rtsp://10.0.0.141:8554", urls={})
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: "ZmFrZQ==")
    monkeypatch.setattr(zeev, "vision_complete", lambda *a, **k: ("Boxes and a boiler.", None))
    zeev.handle_transcript(ctx, "check the basement cam")
    assert any("boiler" in s.lower() for s in ctx.spoke), ctx.spoke


def test_unnamed_camera_asks_which(zeev, ctx, monkeypatch):
    _wyze_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot",
                        lambda *a, **k: pytest.fail("must not grab before asking"))
    zeev.handle_transcript(ctx, "check the camera")
    assert any("which camera" in s.lower() for s in ctx.spoke), ctx.spoke


def test_no_cameras_configured_falls_through_to_llm(zeev, ctx, monkeypatch):
    """With nothing configured the branch must not swallow the turn."""
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", [])
    monkeypatch.setattr(zeev, "wyze_snapshot",
                        lambda *a, **k: pytest.fail("no cameras: must not grab"))
    called = {}
    monkeypatch.setattr(zeev, "_groq_post_with_fallback",
                        lambda *a, **k: called.setdefault("yes", True) and (None, "stop", None))
    try:
        zeev.handle_transcript(ctx, "what's going on upstairs")
    except Exception:
        pass
    assert called.get("yes"), "should have reached the LLM path"


def test_camera_ack_uses_the_turns_voice(zeev, ctx, monkeypatch):
    """A "Hey Ze'ev" camera request must not be announced by Sarina.

    The ack called _speak_device without a voice, so it took the hardcoded
    "sarina" default while the reply used the resolved _LAST_VOICE -- two
    different speakers inside one turn.
    """
    _wyze_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: "ZmFrZQ==")
    monkeypatch.setattr(zeev, "vision_complete", lambda *a, **k: ("A hallway.", None))
    said = []
    ctx._speak_device = lambda text, voice="sarina": said.append((text, voice))
    monkeypatch.setattr(zeev, "_WAKE_VOICE", ["daniel"])
    zeev.handle_transcript(ctx, "what's going on upstairs")
    assert said, "nothing was spoken"
    voices = {v for _, v in said}
    assert voices == {"daniel"}, f"expected one voice, got {said}"


def test_camera_grab_overlaps_the_announcement(zeev, ctx, monkeypatch):
    """The frame grab must run *under* the ack, not after it.

    _speak_device blocks for the duration of the audio (~3.2s measured on the
    Pi). The ack exists precisely because the grab is slow, so running them in
    series made the wait it apologises for ~3s longer. This is pure ordering --
    a functional test still passes with the serial version -- so it needs its
    own pin: the grab has to have *started* before the ack returns.
    """
    _wyze_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    grab_started = threading.Event()
    release_grab = threading.Event()

    def _slow_snap(stream, **kw):
        grab_started.set()
        release_grab.wait(5)
        return "ZmFrZQ=="

    monkeypatch.setattr(zeev, "wyze_snapshot", _slow_snap)
    monkeypatch.setattr(zeev, "vision_complete", lambda *a, **k: ("A hallway.", None))

    overlapped = []

    def _speak(text, voice="sarina"):
        # Runs where the real TTS would block; the grab must already be in
        # flight by now, and must not have needed the ack to finish first.
        overlapped.append(grab_started.wait(5))
        release_grab.set()

    ctx._speak_device = _speak
    zeev.handle_transcript(ctx, "what's going on upstairs")
    assert overlapped and overlapped[0], \
        "grab had not started while the announcement was speaking (still serial)"


# --- Named subject sweep ("check on Smokey") --------------------------------

def _subject_env(zeev, monkeypatch, cams=("basement-cam", "upstairs")):
    _wyze_env(zeev, monkeypatch)
    monkeypatch.setattr(zeev, "WYZE_SUBJECTS", {
        "smokey": {"name": "Smokey", "kind": "cat", "cams": list(cams)}})


def test_subject_sweep_stops_at_the_first_hit(zeev, ctx, monkeypatch):
    _subject_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    looked = []
    monkeypatch.setattr(zeev, "wyze_snapshot",
                        lambda s, **k: looked.append(s) or "ZmFrZQ==")
    monkeypatch.setattr(zeev, "vision_complete",
                        lambda *a, **k: ("FOUND: yes\nA grey cat is asleep on the couch.", None))
    zeev.handle_transcript(ctx, "check on Smokey")
    reply = ctx.spoke[-1].lower()
    assert "smokey" in reply and "basement cam" in reply and "asleep" in reply, ctx.spoke
    # The second grab may have been started speculatively under the first
    # vision call, but no second camera may have been *reported*.
    assert "upstairs" not in reply


def test_subject_sweep_moves_on_after_a_miss(zeev, ctx, monkeypatch):
    _subject_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: f"img-{s}")
    def _vision(img, prompt):
        if img == "img-basement-cam":
            return ("FOUND: no\nAn empty basement.", None)
        return ("FOUND: yes\nThe cat is on the landing.", None)
    monkeypatch.setattr(zeev, "vision_complete", _vision)
    zeev.handle_transcript(ctx, "where's Smokey")
    assert "upstairs" in ctx.spoke[-1].lower(), ctx.spoke
    assert "landing" in ctx.spoke[-1].lower(), ctx.spoke


def test_subject_miss_is_worded_as_not_seen(zeev, ctx, monkeypatch):
    """"I didn't see him", never "he isn't there" -- a small model missing a
    dark cat on a dark couch is the wrong-city failure class."""
    _subject_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: "ZmFrZQ==")
    monkeypatch.setattr(zeev, "vision_complete",
                        lambda *a, **k: ("FOUND: no\nAn empty room.", None))
    zeev.handle_transcript(ctx, "check on Smokey")
    assert "didn't see" in ctx.spoke[-1].lower(), ctx.spoke


def test_subject_replies_never_say_cam_camera(zeev, ctx, monkeypatch):
    """Labels already end in "cam"; appending "camera" is heard, not seen.

    A substring assertion on "basement cam" is satisfied by "basement cam
    camera", so this needs its own pin.
    """
    _subject_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: "ZmFrZQ==")
    for vreply in ("FOUND: yes\nA cat on the couch.", "FOUND: no\nEmpty."):
        monkeypatch.setattr(zeev, "vision_complete", lambda *a, **k: (vreply, None))
        ctx.spoke.clear()
        zeev.handle_transcript(ctx, "check on Smokey")
        assert not any("cam camera" in s.lower() for s in ctx.spoke), ctx.spoke


def test_inconclusive_read_is_not_announced_as_a_miss(zeev, ctx, monkeypatch):
    """Saying "not on the basement cam" and then describing the cat on the
    basement cam is Zeev contradicting itself inside one turn."""
    _subject_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: f"img-{s}")
    def _vision(img, prompt):
        if img == "img-basement-cam":
            return ("A grey cat is on the stairs.", None)   # no FOUND line
        return ("FOUND: no\nEmpty landing.", None)
    monkeypatch.setattr(zeev, "vision_complete", _vision)
    zeev.handle_transcript(ctx, "check on Smokey")
    assert not any("not on the basement" in s.lower() for s in ctx.spoke), ctx.spoke
    assert "grey cat" in ctx.spoke[-1].lower(), ctx.spoke


def test_subject_reports_a_dead_camera_as_such(zeev, ctx, monkeypatch):
    """No frame at all must not be reported as "I didn't see Smokey"."""
    _subject_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: None)
    monkeypatch.setattr(zeev, "vision_complete",
                        lambda *a, **k: pytest.fail("no frame: must not call vision"))
    zeev.handle_transcript(ctx, "check on Smokey")
    assert "couldn't get a picture" in ctx.spoke[-1].lower(), ctx.spoke


def test_named_room_narrows_the_sweep(zeev, ctx, monkeypatch):
    _subject_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    looked = []
    monkeypatch.setattr(zeev, "wyze_snapshot",
                        lambda s, **k: looked.append(s) or "ZmFrZQ==")
    monkeypatch.setattr(zeev, "vision_complete",
                        lambda *a, **k: ("FOUND: no\nAn empty basement.", None))
    zeev.handle_transcript(ctx, "check on Smokey in the basement cam")
    assert looked == ["basement-cam"], looked


def test_subject_ack_and_progress_use_the_turns_voice(zeev, ctx, monkeypatch):
    _subject_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: "ZmFrZQ==")
    monkeypatch.setattr(zeev, "vision_complete",
                        lambda *a, **k: ("FOUND: no\nEmpty.", None))
    said = []
    ctx._speak_device = lambda text, voice="sarina": said.append((text, voice))
    monkeypatch.setattr(zeev, "_WAKE_VOICE", ["daniel"])
    zeev.handle_transcript(ctx, "check on Smokey")
    assert said and {v for _, v in said} == {"daniel"}, said


def test_subject_reminder_goes_to_the_tool_branch(zeev, ctx, monkeypatch):
    """This branch sits above the tool branch -- it must not swallow reminders."""
    _subject_env(zeev, monkeypatch)
    monkeypatch.setattr(zeev, "wyze_snapshot",
                        lambda *a, **k: pytest.fail("a reminder must not grab a frame"))
    reached = {}
    monkeypatch.setattr(zeev, "_groq_post",
                        lambda *a, **k: reached.setdefault("yes", True) and (None, "stop"))
    monkeypatch.setattr(zeev, "_groq_post_with_fallback",
                        lambda *a, **k: reached.setdefault("yes", True) and (None, "stop", None))
    try:
        zeev.handle_transcript(ctx, "remind me to check on Smokey at four")
    except Exception:
        pass
    assert reached.get("yes"), "reminder never reached the LLM/tool path"


def test_no_subjects_configured_falls_through(zeev, ctx, monkeypatch):
    _wyze_env(zeev, monkeypatch)
    monkeypatch.setattr(zeev, "WYZE_SUBJECTS", {})
    monkeypatch.setattr(zeev, "wyze_snapshot",
                        lambda *a, **k: pytest.fail("no subjects: must not grab"))
    reached = {}
    monkeypatch.setattr(zeev, "_groq_post_with_fallback",
                        lambda *a, **k: reached.setdefault("yes", True) and (None, "stop", None))
    try:
        zeev.handle_transcript(ctx, "check on Smokey")
    except Exception:
        pass
    assert reached.get("yes")


def test_all_stage_direction_reply_still_says_something(zeev, ctx, monkeypatch):
    """Observed live: the model returned only "(Sarina's voice...)" and nothing
    else. Stripping that leaves an empty string, and empty is spoken as silence.
    """
    _wyze_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: "ZmFrZQ==")
    monkeypatch.setattr(zeev, "vision_complete",
                        lambda *a, **k: ("(Sarina's voice, calm and professional, "
                                         "delivers Zeev's words.)", None))
    zeev.handle_transcript(ctx, "what's going on upstairs")
    spoken = " ".join(ctx.spoke)
    assert zeev._clean_for_tts(spoken).strip(), f"turn spoke silence: {ctx.spoke}"
    assert "couldn't make anything out" in spoken.lower(), ctx.spoke


# --- Multi-camera sweep ("check all cameras") --------------------------------
#
# Before this the phrase reached the "Which camera?" ask -- honest, but not the
# question. These pin the whole branch, not just the regex: that it grabs every
# camera, speaks each one's answer, and never overstates a dead one.

def _sweep_env(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", ["smokeys-cam", "bedroom-cam"])
    monkeypatch.setattr(zeev, "WYZE_RTSP_BASE", "")
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS",
                        {"smokeys-cam": "rtsp://10.0.0.217:554/stream0",
                         "bedroom-cam": "rtsp://10.0.0.84:554/stream0"})
    monkeypatch.setattr(zeev, "WYZE_SUBJECTS", {})


def test_sweep_looks_at_every_camera_and_reports_both(zeev, ctx, monkeypatch):
    _sweep_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    looked = []
    monkeypatch.setattr(zeev, "wyze_snapshot",
                        lambda s, **k: looked.append(s) or f"img-{s}")
    monkeypatch.setattr(zeev, "vision_complete",
                        lambda img, p: (f"A tidy room seen from {img}.", None))
    zeev.handle_transcript(ctx, "check all cameras")
    assert sorted(looked) == ["bedroom-cam", "smokeys-cam"], looked
    reply = ctx.spoke[-1].lower()
    assert "bedroom cam" in reply and "smokeys cam" in reply, ctx.spoke
    assert "which camera" not in reply, "sweep must not fall back to the ask path"


def test_sweep_does_not_ask_which_camera(zeev, ctx, monkeypatch):
    """The regression this feature replaces."""
    _sweep_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: f"img-{s}")
    monkeypatch.setattr(zeev, "vision_complete", lambda img, p: ("An empty room.", None))
    for text in ["check all cameras", "check both cams", "what do you see on all the cameras"]:
        ctx.spoke.clear()
        zeev.handle_transcript(ctx, text)
        assert "which camera" not in ctx.spoke[-1].lower(), (text, ctx.spoke)


def test_sweep_names_a_camera_it_could_not_reach(zeev, ctx, monkeypatch):
    """Silently dropping a dead camera reads as a full report of the house."""
    _sweep_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot",
                        lambda s, **k: None if s == "bedroom-cam" else f"img-{s}")
    monkeypatch.setattr(zeev, "vision_complete", lambda img, p: ("A cat on the sill.", None))
    zeev.handle_transcript(ctx, "check all cameras")
    reply = ctx.spoke[-1].lower()
    assert "cat on the sill" in reply
    assert "bedroom cam" in reply and "couldn't get a picture" in reply, ctx.spoke


def test_sweep_with_no_frames_at_all_says_so(zeev, ctx, monkeypatch):
    _sweep_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: None)
    monkeypatch.setattr(zeev, "vision_complete",
                        lambda *a, **k: pytest.fail("no frame: must not call vision"))
    zeev.handle_transcript(ctx, "check all cameras")
    reply = ctx.spoke[-1].lower()
    assert "couldn't get a picture" in reply and "asleep or offline" in reply, ctx.spoke


def test_sweep_drops_an_all_stage_direction_reply(zeev, ctx, monkeypatch):
    """A vision reply can be entirely stage direction; stripped it is empty,
    which would be spoken as silence in the middle of a multi-camera answer."""
    _sweep_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: f"img-{s}")
    monkeypatch.setattr(zeev, "vision_complete",
                        lambda img, p: (("(Sarina's calm voice)", None)
                                        if "bedroom" in img else ("A lamp is on.", None)))
    zeev.handle_transcript(ctx, "check all cameras")
    reply = ctx.spoke[-1].lower()
    assert "lamp is on" in reply
    assert "sarina" not in reply, ctx.spoke
    assert "bedroom cam" in reply and "couldn't get a picture" in reply


def test_named_room_still_beats_a_sweep_phrase(zeev, ctx, monkeypatch):
    _sweep_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    looked = []
    monkeypatch.setattr(zeev, "wyze_snapshot",
                        lambda s, **k: looked.append(s) or f"img-{s}")
    monkeypatch.setattr(zeev, "vision_complete", lambda img, p: ("An empty room.", None))
    zeev.handle_transcript(ctx, "check all the cameras in the bedroom cam")
    assert looked == ["bedroom-cam"], looked


def test_sweep_announces_before_the_slow_part(zeev, ctx, monkeypatch):
    """~50s of silence for two cameras reads as a dead device."""
    _sweep_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: f"img-{s}")
    monkeypatch.setattr(zeev, "vision_complete", lambda img, p: ("An empty room.", None))
    zeev.handle_transcript(ctx, "check both cameras")
    assert "let me check both cameras" in ctx.spoke[0].lower(), ctx.spoke


def test_mangled_trigger_still_reaches_the_camera_branch(zeev, ctx, monkeypatch):
    """Whisper mangles the verb, not the noun.

    Live 2026-07-30 it heard "check all cameras" as "checko cameras". No verb
    survived, the turn reached the LLM, and the 8B repeated the *previous*
    turn's genuine camera description as a current observation -- accurate
    enough to pass for working, which is what makes it dangerous.
    """
    _sweep_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "CAMERA_AVAILABLE", False)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: f"img-{s}")
    monkeypatch.setattr(zeev, "vision_complete", lambda img, p: ("A room.", None))
    for text in ["checko cameras", "cameras", "the cameras"]:
        ctx.spoke.clear()
        zeev.handle_transcript(ctx, text)   # _no_llm fails the test if it falls through
        assert ctx.spoke, text


def test_camera_noun_in_a_reminder_still_goes_to_the_tool_branch(zeev, ctx, monkeypatch):
    """"remind me to buy a camera" is a reminder -- the same guard
    resolve_subject uses, for the same reason."""
    _sweep_env(zeev, monkeypatch)
    monkeypatch.setattr(zeev, "CAMERA_AVAILABLE", False)
    monkeypatch.setattr(zeev, "wyze_snapshot",
                        lambda *a, **k: pytest.fail("reminder must not grab a frame"))
    monkeypatch.setattr(zeev, "vision_complete",
                        lambda *a, **k: pytest.fail("reminder must not call vision"))
    monkeypatch.setattr(zeev, "_groq_post", lambda *a, **k: (None, "stop here"))
    monkeypatch.setattr(zeev, "_groq_post_with_fallback", lambda *a, **k: (None, "stop here"))
    zeev.handle_transcript(ctx, "remind me to buy a camera tomorrow")


def test_single_camera_reply_keeps_the_stripped_text(zeev, ctx, monkeypatch):
    """The stripped value used to be computed and thrown away -- used only as an
    emptiness test -- so a partial stage direction was stored verbatim. Live row
    1906: "(Sarina's voice, composed and professional): I see a bedroom...".
    That then sits in the session and in RAG as though it were an observation.
    """
    _sweep_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: "img")
    monkeypatch.setattr(zeev, "vision_complete", lambda img, p: (
        "(Sarina's voice, composed and professional): I see a bedroom.", None))
    zeev.handle_transcript(ctx, "check the bedroom cam")
    stored = ctx.session[-1]["content"]
    assert "sarina" not in stored.lower(), stored
    assert "I see a bedroom." in stored


def test_all_stage_direction_reply_falls_back(zeev, ctx, monkeypatch):
    """Stripping a reply that is *only* stage direction leaves nothing, and an
    empty reply is spoken as silence."""
    _sweep_env(zeev, monkeypatch)
    _no_llm(monkeypatch, zeev)
    monkeypatch.setattr(zeev, "wyze_snapshot", lambda s, **k: "img")
    monkeypatch.setattr(zeev, "vision_complete",
                        lambda img, p: ("(Sarina's voice delivers Zeev's words.)", None))
    zeev.handle_transcript(ctx, "check the bedroom cam")
    stored = ctx.session[-1]["content"]
    assert "couldn't make anything out" in stored.lower(), stored


# --- Goodnight (two voices) --------------------------------------------------
#
# The only branch that answers in two voices: Zeev (daniel) then Sarina. Every
# other reply is spoken by finish_turn in a single voice, which is why this one
# needs finish_turn(speak=False) -- the tail still records the text.

def _voices(ctx):
    """Re-wire _speak_device to capture (text, voice) pairs."""
    said = []
    ctx._speak_device = lambda text, voice="sarina": said.append((text, voice))
    return said


def test_goodnight_answers_in_both_voices(zeev, ctx, monkeypatch):
    _no_llm(monkeypatch, zeev)
    said = _voices(ctx)
    zeev.handle_transcript(ctx, "goodnight")
    assert [v for _, v in said] == ["daniel", "sarina"], said
    assert all(t.strip() for t, _ in said), "neither line may be empty"


def test_goodnight_records_both_lines_once(zeev, ctx, monkeypatch):
    """speak=False must not cost the history: both lines belong in the record,
    and the tail must not speak them a third time in one voice."""
    _no_llm(monkeypatch, zeev)
    said = _voices(ctx)
    zeev.handle_transcript(ctx, "goodnight")
    assert len(said) == 2, said
    stored = ctx.session[-1]["content"]
    for text, _ in said:
        assert text in stored, stored
    assert [m["role"] for m in ctx.session] == ["user", "assistant"]


def test_goodnight_ignores_the_wake_voice(zeev, ctx, monkeypatch):
    """Both answer whichever one was woken -- that is the point of the branch."""
    _no_llm(monkeypatch, zeev)
    said = _voices(ctx)
    zeev._WAKE_VOICE[0] = "daniel"
    try:
        zeev.handle_transcript(ctx, "goodnight")
        assert [v for _, v in said] == ["daniel", "sarina"], said
    finally:
        zeev._WAKE_VOICE[0] = None


@pytest.mark.parametrize("text", [
    "goodnight", "good night", "Goodnight Zeev", "night night",
    "sweet dreams", "I'm going to bed", "heading to sleep",
    "turning in for the night", "time for bed", "calling it a night",
])
def test_goodnight_phrasings(zeev, ctx, monkeypatch, text):
    _no_llm(monkeypatch, zeev)
    said = _voices(ctx)
    zeev.handle_transcript(ctx, text)
    assert len(said) == 2, (text, said)


@pytest.mark.parametrize("text", [
    "remind me to say goodnight at nine",          # a reminder, not a sign-off
    "what's a good night light for a kid's room",  # not a sign-off either
    "tell me about sleep apnea",
])
def test_goodnight_does_not_over_fire(zeev, text):
    """The gate reads only the first 40 chars and excludes tool phrasing, the
    same shape resolve_subject uses for exactly this reason."""
    head = text[:40]
    fires = bool(zeev._GOODNIGHT_RE.search(head)) and not bool(
        zeev._TOOL_INTENT_RE.search(text))
    assert not fires, text


def test_goodnight_always_names_the_household(zeev, ctx, monkeypatch):
    """Every pair must name everyone. A random choice that sometimes dropped
    someone would make the wish intermittent, which reads worse than not having
    it -- so this checks the table, not one sampled reply."""
    for zeev_line, sarina_line in zeev._GOODNIGHT_LINES:
        both = f"{zeev_line} {sarina_line}"
        for who in zeev._GOODNIGHT_HOUSEHOLD:
            assert who in both, f"{who} missing from {both!r}"


def test_both_voices_wish_alex_goodnight(zeev):
    """Being wished goodnight only by whichever voice speaks first is not being
    wished goodnight by both."""
    for zeev_line, sarina_line in zeev._GOODNIGHT_LINES:
        assert "Alex" in zeev_line, zeev_line
        assert "Alex" in sarina_line, sarina_line


def test_goodnight_household_reaches_the_spoken_reply(zeev, ctx, monkeypatch):
    _no_llm(monkeypatch, zeev)
    said = _voices(ctx)
    zeev.handle_transcript(ctx, "goodnight")
    spoken = " ".join(t for t, _ in said)
    for who in zeev._GOODNIGHT_HOUSEHOLD:
        assert who in spoken, (who, spoken)
    assert who in ctx.session[-1]["content"]


# ---------------------------------------------------------------------------
# Token budget vs script cost
# ---------------------------------------------------------------------------

def _capture_tok_limit(zeev, monkeypatch, ctx, transcript):
    """Drive one LLM turn and return the max_tokens it asked for."""
    seen = {}

    def fake_post(msgs, model, stream=True, max_tokens=400, **kw):
        seen["max_tokens"] = max_tokens
        return None, "offline"          # short-circuit before any streaming

    monkeypatch.setattr(zeev, "_groq_post_with_fallback", fake_post)
    monkeypatch.setattr(zeev, "_groq_post", fake_post)
    monkeypatch.setattr(zeev, "_build_system_prompt", lambda *a, **k: "sys")
    zeev.handle_transcript(ctx, transcript)
    return seen.get("max_tokens")


def test_hebrew_content_gets_a_bigger_token_budget(zeev, monkeypatch, ctx):
    """Live 2026-07-31: the Hebrew line stopped mid-phrase with an unclosed
    quote, which sounds exactly like TTS being cut off and is not.

    Device mode caps replies at 160 tokens because they are spoken aloud, but
    that is tuned for English. A token is not a unit of content: pointed Hebrew
    costs several tokens per character, so the same sentence blows the budget.
    """
    tok = _capture_tok_limit(
        zeev, monkeypatch, ctx,
        "help me with the angelic prayer, say it in Hebrew")
    assert tok is not None, "never reached the LLM"
    assert tok >= 500, f"Hebrew recitation got only {tok} tokens"


def test_ordinary_english_stays_terse(zeev, monkeypatch, ctx):
    """The bigger budget must not leak into normal chat — every reply here is
    spoken, and 500 tokens of speech is well over a minute of talking."""
    tok = _capture_tok_limit(zeev, monkeypatch, ctx, "what's the weather like")
    assert tok is not None, "never reached the LLM"
    assert tok <= 200, f"ordinary chat inflated to {tok} tokens"


# ---------------------------------------------------------------------------
# "Want to hear more?" when the reply ran out of room
# ---------------------------------------------------------------------------

class _FakeResp:
    status_code = 200

    def __init__(self, text, streaming=False):
        self._text = text
        # handle_transcript takes the streaming path only when resp.raw exists
        # (`STREAM_TTS and getattr(resp, "raw", None) is not None`), so the
        # attribute is what selects which branch this drives.
        self.raw = object() if streaming else None

    def json(self):
        return {"choices": [{"message": {"content": self._text}}]}


def _run_llm_turn(zeev, monkeypatch, ctx, reply_text, streamed=False, followup=""):
    """Drive one LLM turn with a canned reply. streamed=True makes
    _stream_speak return the text, mimicking STREAM_TTS=1 in production."""
    monkeypatch.setattr(zeev, "_build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(zeev, "_groq_post_with_fallback",
                        lambda *a, **k: (_FakeResp(reply_text, streamed), None))
    monkeypatch.setattr(zeev, "_groq_post",
                        lambda *a, **k: (_FakeResp(reply_text, streamed), None))
    if streamed:
        ctx._stream_speak = lambda *a, **k: (
            ctx.spoke.append(reply_text) or reply_text)
    ctx._followup_listen = lambda: followup
    zeev.handle_transcript(ctx, "tell me about the siddur")
    return ctx


def test_offer_is_appended_when_the_reply_was_cut_off(zeev, monkeypatch, ctx):
    """Live 2026-07-31: the Hebrew prayer reply ended mid-phrase with an
    unclosed quote and nothing followed it.

    Rule 3 of the VOICE INTERFACE prompt asks the model to offer more, but that
    is prompt compliance and the 8B skips it. A dropped dangling fragment is
    hard evidence it was still talking, so the offer is deterministic.
    """
    _run_llm_turn(zeev, monkeypatch, ctx,
                  "The first part is complete. But the second part runs out of ro")
    assert any("Want to hear more?" in s for s in ctx.spoke), ctx.spoke
    assert ctx.session[-1]["content"].endswith("Want to hear more?"), ctx.session[-1]


def test_offer_is_spoken_on_the_streaming_path_too(zeev, monkeypatch, ctx):
    """STREAM_TTS=1 is the default, and by this point the stream has already
    spoken every complete sentence. Appending to speak_text alone would leave
    the offer in the log only -- and the follow-up listener would then wait for
    an answer to a question the user never heard."""
    _run_llm_turn(zeev, monkeypatch, ctx,
                  "The first part is complete. But the second runs out of ro",
                  streamed=True)
    assert any(s.strip() == "Want to hear more?" for s in ctx.spoke), ctx.spoke


def test_no_offer_when_the_reply_finished_cleanly(zeev, monkeypatch, ctx):
    """A complete reply must not get a spurious offer -- there is nothing held
    back to deliver, and the follow-up listener would open the mic for nothing."""
    _run_llm_turn(zeev, monkeypatch, ctx, "The siddur is the Jewish prayer book.")
    assert not any("Want to hear more?" in s for s in ctx.spoke), ctx.spoke


def test_pending_detail_topic_does_not_compound_on_repeated_failure(zeev, monkeypatch, ctx):
    """Live 2026-08-06: two consecutive failed pre-generations (OpenRouter
    free-tier candidate rate-limited) produced "<topic> Give me more detail
    on that. Give me more detail on that." -- the fallback re-ask rewrites
    `transcript` for THIS turn's own routing, and that rewritten value was
    being stored as the NEXT round's topic too, so the suffix compounded
    every consecutive failure. The stored topic must stay clean regardless
    of how many rounds fail in a row."""
    monkeypatch.setattr(zeev, "_build_system_prompt", lambda *a, **k: "sys")
    # This turn's own reply also ends in "Want to hear more?" so a NEW
    # pending-detail round gets armed after the fallback re-ask completes.
    reply = "Ezekiel saw a vision of a divine chariot. Want to hear more?"
    monkeypatch.setattr(zeev, "_groq_post_with_fallback",
                        lambda *a, **k: (_FakeResp(reply), None))
    monkeypatch.setattr(zeev, "_groq_post",
                        lambda *a, **k: (_FakeResp(reply), None))

    # Simulate: pre-generation already failed once for a prior "want to hear
    # more?" offer on this clean topic (mirrors _prefetch_detail's failure
    # path -- pending[0] stays None, ready is set so the wait doesn't block).
    ctx._pending_detail[0] = None
    ctx._pending_detail_source[0] = "tell me about ezekiel"
    ctx._pending_detail_ready.set()
    # None (not a lambda returning "") so `getattr(ctx, "_followup_listen",
    # None)` is falsy and the end-of-turn follow-up-listen block is skipped
    # entirely -- that block correctly clears pending state on a non-answer,
    # which would otherwise mask the actual thing under test: the value
    # _pending_detail_source[0] gets set to earlier in this same turn.
    ctx._followup_listen = None

    zeev.handle_transcript(ctx, "yes")

    assert ctx._pending_detail_source[0] == "tell me about ezekiel", (
        f"topic drifted to {ctx._pending_detail_source[0]!r}"
    )


def test_model_supplied_offer_is_not_duplicated(zeev, monkeypatch, ctx):
    """When the model does comply, the offer must not be appended twice."""
    _run_llm_turn(zeev, monkeypatch, ctx,
                  "The siddur is the Jewish prayer book. Want to hear more?")
    spoken = " ".join(ctx.spoke)
    assert spoken.lower().count("want to hear more") == 1, ctx.spoke


# ---------------------------------------------------------------------------
# Answering a question Zeev asked, without a wake word
# ---------------------------------------------------------------------------

def test_any_question_opens_the_mic(zeev, monkeypatch, ctx):
    """Live 2026-08-01: Sarina ended with "Would you like to practice reciting
    it together?" and closed the mic, so Alex had to wake the device to answer
    a question it had just asked.

    Only "Want to hear more?" armed the listener, because it was the sole
    caller of the pending-detail machinery.
    """
    _run_llm_turn(zeev, monkeypatch, ctx,
                  "Shall we practice it together?",
                  followup="yes let's practice")
    # The answer came back through as a second turn rather than being dropped.
    users = [m["content"] for m in ctx.session if m["role"] == "user"]
    assert "yes let's practice" in users, users


def test_statement_does_not_open_the_mic(zeev, monkeypatch, ctx):
    """A reply that asked nothing must not sit there recording the room."""
    called = []
    _run_llm_turn(zeev, monkeypatch, ctx, "The siddur is the prayer book.",
                  followup="this should never be heard")
    ctx._followup_listen = lambda: called.append(1) or ""
    assert not any(m["content"] == "this should never be heard"
                   for m in ctx.session), ctx.session


def test_followup_noise_is_not_a_turn(zeev, monkeypatch, ctx):
    """A spurious recording transcribes as confident nonsense; without the
    \\w{2,} guard it becomes a real LLM turn and keeps the loop fed."""
    _run_llm_turn(zeev, monkeypatch, ctx, "Shall we practice it together?",
                  followup="A")
    users = [m["content"] for m in ctx.session if m["role"] == "user"]
    assert "A" not in users, users


def test_followup_chain_is_depth_capped(zeev, monkeypatch, ctx):
    """A model that ends every reply with a question would otherwise hold the
    mic in a loop the user cannot walk away from."""
    _run_llm_turn(zeev, monkeypatch, ctx, "Shall we keep going?",
                  followup="and then what happens")
    users = [m["content"] for m in ctx.session if m["role"] == "user"]
    assert users.count("and then what happens") <= zeev._FOLLOWUP_MAX_DEPTH, users


def test_reply_invites_an_answer(zeev):
    assert zeev._reply_invites_an_answer("Want to hear more?")
    assert zeev._reply_invites_an_answer("Shall we practice together?  ")
    assert not zeev._reply_invites_an_answer("The siddur is the prayer book.")
    assert not zeev._reply_invites_an_answer("")


def test_question_followed_by_an_offer_still_invites(zeev):
    """The branch replies this exists for put the question FIRST and trail an
    offer after it, so a tail-only endswith("?") test missed the exact case it
    was written for."""
    assert zeev._reply_invites_an_answer(
        "Which camera? I can check bedroom cam, smokeys cam")
    assert zeev._reply_invites_an_answer("Want to hear more? I have plenty.")


def test_rhetorical_question_mid_passage_does_not_invite(zeev):
    """A long answer that merely contains a question mark is not an
    invitation -- otherwise the mic opens after ordinary exposition."""
    assert not zeev._reply_invites_an_answer(
        "Why do we say it at night? Because the rabbis taught that sleep is "
        "one sixtieth of death, and the verses are a protection recited before "
        "sleeping, which is why the service closes with them and not earlier.")


# ---------------------------------------------------------------------------
# finish_turn branches ask questions too
# ---------------------------------------------------------------------------

def test_branch_question_opens_the_mic(zeev, monkeypatch, ctx):
    """"Which camera? I can check bedroom cam, smokeys cam" is the same
    dead-mic annoyance the LLM path had -- every early-returning branch goes
    through finish_turn, which never armed the listener."""
    monkeypatch.setattr(zeev, "_build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(zeev, "_groq_post_with_fallback",
                        lambda *a, **k: (_FakeResp("The bedroom cam shows a bed."), None))
    monkeypatch.setattr(zeev, "_groq_post",
                        lambda *a, **k: (_FakeResp("The bedroom cam shows a bed."), None))
    ctx._followup_listen = lambda: "the bedroom one"
    zeev._TURN_DEPTH[0] = 0
    zeev.finish_turn(ctx, "Which camera? I can check bedroom cam, smokeys cam.")
    users = [m["content"] for m in ctx.session if m["role"] == "user"]
    assert "the bedroom one" in users, users


def test_branch_statement_does_not_open_the_mic(zeev, ctx):
    called = []
    ctx._followup_listen = lambda: called.append(1) or "should not be heard"
    zeev._TURN_DEPTH[0] = 0
    zeev.finish_turn(ctx, "Playing that now.")
    assert not called, "listened after a branch statement"


def test_unspoken_branch_reply_does_not_open_the_mic(zeev, ctx):
    """speak=False means the branch already said its piece (goodnight speaks in
    two voices itself), so finish_turn has not just uttered this text."""
    called = []
    ctx._followup_listen = lambda: called.append(1) or "x"
    zeev._TURN_DEPTH[0] = 0
    zeev.finish_turn(ctx, "Shall we?", speak=False)
    assert not called, "listened after a reply it never spoke"


def test_branch_followup_respects_the_depth_cap(zeev, ctx):
    """At the cap the mic must stay shut, or a branch that always ends in a
    question loops forever at depth 0."""
    called = []
    ctx._followup_listen = lambda: called.append(1) or "again?"
    zeev._TURN_DEPTH[0] = zeev._FOLLOWUP_MAX_DEPTH
    zeev.finish_turn(ctx, "Shall we keep going?")
    assert not called, "listened past the depth cap"
