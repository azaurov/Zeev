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
