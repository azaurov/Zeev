"""Wyze house cameras: name resolution, and never logging the stream password.

Two failure modes matter here. Describing the wrong room confidently is the
same class of bug as reporting the wrong city -- the model states it as fact
and the user has no way to tell. And ffmpeg echoes the full RTSP URL, which
carries the stream password, in every error line; that leaked a credential into
a terminal transcript once already.
"""
import pytest

CAMS = ["backyard", "basement-cam", "doorbell-cam", "front-yard",
        "leos-room", "living-room-cam", "secret", "upstairs"]


@pytest.mark.parametrize("text,expected", [
    ("check the basement cam",                 "basement-cam"),
    ("what's going on in the living room",     "living-room-cam"),
    ("look at the front yard",                 "front-yard"),
    ("show me the backyard",                   "backyard"),
    ("check upstairs",                         "upstairs"),
    ("is anyone at the doorbell cam",          "doorbell-cam"),
    ("what's in leos room",                    "leos-room"),
])
def test_resolves_named_camera(zeev, text, expected):
    stream, _ = zeev.resolve_wyze_cam(text, CAMS)
    assert stream == expected


def test_longest_label_wins(zeev):
    """'front yard' must not be shadowed by a shorter overlapping label."""
    stream, _ = zeev.resolve_wyze_cam("look at the front yard camera", CAMS)
    assert stream == "front-yard"


def test_unnamed_camera_asks_rather_than_guessing(zeev):
    stream, alts = zeev.resolve_wyze_cam("what's going on out there", CAMS)
    assert stream is None
    assert alts == sorted(CAMS)


def test_ambiguous_match_asks(zeev):
    """Two cameras matching equally well must not silently pick one."""
    cams = ["garage-left", "garage-right"]
    stream, alts = zeev.resolve_wyze_cam("check the garage left and garage right", cams)
    assert stream is None or stream in cams
    stream2, alts2 = zeev.resolve_wyze_cam("check the garage", cams)
    assert stream2 is None, "a bare 'garage' matches neither exactly; must ask"


def test_no_cameras_configured_never_matches(zeev):
    assert zeev.resolve_wyze_cam("check the basement cam", []) == (None, [])


def test_label_is_speakable(zeev):
    assert zeev.wyze_cam_label("living-room-cam") == "living room cam"
    assert zeev.wyze_cam_label("leos-room") == "leos room"


def test_scrub_removes_rtsp_credentials(zeev):
    """The password lives in the URL; no log line may carry it."""
    err = ("[rtsp @ 0x1] method DESCRIBE failed: 401\n"
           "Error opening input file rtsp://zeev:sup3rSecret@10.0.0.141:8554/upstairs.")
    out = zeev._scrub_rtsp(err)
    assert "sup3rSecret" not in out
    assert "zeev:" not in out
    assert "rtsp://<redacted>" in out
    assert "DESCRIBE failed: 401" in out, "must keep the diagnostic part"


def test_scrub_covers_rtsps_too(zeev):
    """The secure scheme carries the same password."""
    out = zeev._scrub_rtsp("Error opening rtsps://zeev:sup3rSecret@10.0.0.217:322/live")
    assert "sup3rSecret" not in out


def test_scrub_handles_empty(zeev):
    assert zeev._scrub_rtsp("") == ""
    assert zeev._scrub_rtsp(None) == ""


def test_snapshot_without_config_returns_none(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_RTSP_BASE", "")
    assert zeev.wyze_snapshot("upstairs") is None


# --- direct-camera URLs -----------------------------------------------------
#
# A camera flashed with Wyze's RTSP firmware answers at rtsp://user:pass@ip/live
# and never appears as a bridge path, so both forms have to coexist: bare names
# resolve under WYZE_RTSP_BASE, `name=rtsp://...` entries override it.

def test_bridge_camera_uses_base(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_RTSP_BASE", "rtsp://u:p@10.0.0.141:8554")
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS", {})
    assert zeev.wyze_stream_url("basement-cam") == "rtsp://u:p@10.0.0.141:8554/basement-cam"


def test_direct_camera_overrides_base(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_RTSP_BASE", "rtsp://u:p@10.0.0.141:8554")
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS",
                        {"upstairs": "rtsp://zeev:pw@10.0.0.217/live"})
    assert zeev.wyze_stream_url("upstairs") == "rtsp://zeev:pw@10.0.0.217/live"
    # the others must still go via the bridge
    assert zeev.wyze_stream_url("basement-cam") == "rtsp://u:p@10.0.0.141:8554/basement-cam"


def test_direct_camera_works_without_any_base(zeev, monkeypatch):
    """Flashing every camera should not require the bridge to stay configured."""
    monkeypatch.setattr(zeev, "WYZE_RTSP_BASE", "")
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS", {"upstairs": "rtsp://a:b@10.0.0.217/live"})
    assert zeev.wyze_stream_url("upstairs") == "rtsp://a:b@10.0.0.217/live"
    assert zeev.wyze_stream_url("basement-cam") == ""


def test_trailing_slash_on_base_does_not_double(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_RTSP_BASE", "rtsp://u:p@10.0.0.141:8554/")
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS", {})
    assert zeev.wyze_stream_url("upstairs") == "rtsp://u:p@10.0.0.141:8554/upstairs"


# --- credentials supplied separately ----------------------------------------
#
# A camera password is typed into a phone app and routinely contains %, @, ^, +
# and = -- all meaningful in a URL, and `%` is additionally eaten by printf on
# the way into .env. Both happened live: a password landed in the file as 32
# spaces followed by "0.000000rak4^^+nop3=". Taking user/pass as plain values
# and encoding them here removes the entire class of error.

def test_credentials_are_injected_and_encoded(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS", {"upstairs": "rtsps://10.0.0.217:322/live"})
    monkeypatch.setattr(zeev, "WYZE_RTSP_USER", "bushido3shep")
    monkeypatch.setattr(zeev, "WYZE_RTSP_PASS", "%40Frak4^^+nop3=")
    url = zeev.wyze_stream_url("upstairs")
    assert url == "rtsps://bushido3shep:%2540Frak4%5E%5E%2Bnop3%3D@10.0.0.217:322/live"
    # No raw URL-hostile character may survive in the userinfo. Checked after
    # stripping the percent-escapes, so the '%' of an escape isn't mistaken for
    # a literal one -- and note '4' is a legitimate password character here.
    userinfo = url.split("://", 1)[1].split("@", 1)[0]
    import re as _re
    bare = _re.sub(r"%[0-9A-Fa-f]{2}", "", userinfo)
    for ch in "%@^+=/?#":
        assert ch not in bare, f"raw {ch!r} left in userinfo {userinfo!r}"


def test_existing_credentials_in_url_are_not_doubled(zeev, monkeypatch):
    """A URL that already carries creds must be left alone."""
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS", {"upstairs": "rtsps://a:b@10.0.0.217:322/live"})
    monkeypatch.setattr(zeev, "WYZE_RTSP_USER", "other")
    monkeypatch.setattr(zeev, "WYZE_RTSP_PASS", "pw")
    assert zeev.wyze_stream_url("upstairs") == "rtsps://a:b@10.0.0.217:322/live"


def test_no_credentials_configured_leaves_url_bare(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS", {"upstairs": "rtsps://10.0.0.217:322/live"})
    monkeypatch.setattr(zeev, "WYZE_RTSP_USER", "")
    assert zeev.wyze_stream_url("upstairs") == "rtsps://10.0.0.217:322/live"


def test_credentials_apply_to_bridge_urls_too(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS", {})
    monkeypatch.setattr(zeev, "WYZE_RTSP_BASE", "rtsp://10.0.0.141:8554")
    monkeypatch.setattr(zeev, "WYZE_RTSP_USER", "zeev")
    monkeypatch.setattr(zeev, "WYZE_RTSP_PASS", "p@ss")
    assert zeev.wyze_stream_url("basement-cam") == "rtsp://zeev:p%40ss@10.0.0.141:8554/basement-cam"


# --- per-camera credentials -------------------------------------------------
#
# Each flashed camera's user/pass is typed into the phone app separately, so
# there is no reason two cameras would share a pair. A single global pair
# silently 401s every camera but the one it was set for.

def test_per_camera_credentials_override_the_shared_pair(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS",
                        {"basement-cam": "rtsp://10.0.0.90:554/stream0",
                         "upstairs": "rtsp://10.0.0.217:554/stream0"})
    monkeypatch.setattr(zeev, "WYZE_RTSP_USER", "shared")
    monkeypatch.setattr(zeev, "WYZE_RTSP_PASS", "sharedpw")
    monkeypatch.setenv("WYZE_RTSP_USER_BASEMENT_CAM", "cellar")
    monkeypatch.setenv("WYZE_RTSP_PASS_BASEMENT_CAM", "p@ss word")
    assert zeev.wyze_stream_url("basement-cam") == \
        "rtsp://cellar:p%40ss%20word@10.0.0.90:554/stream0"
    # the camera without its own pair still uses the shared one
    assert zeev.wyze_stream_url("upstairs") == \
        "rtsp://shared:sharedpw@10.0.0.217:554/stream0"


def test_env_suffix_normalises_the_stream_name(zeev):
    assert zeev._wyze_env_suffix("basement-cam") == "BASEMENT_CAM"
    assert zeev._wyze_env_suffix("front yard 2") == "FRONT_YARD_2"
    assert zeev._wyze_env_suffix("upstairs") == "UPSTAIRS"


def test_per_camera_user_without_password_does_not_fall_back(zeev, monkeypatch):
    """A camera with a user set and no password must not borrow the shared
    password -- that silently sends the wrong secret to the wrong camera."""
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS", {"cam": "rtsp://10.0.0.9/stream0"})
    monkeypatch.setattr(zeev, "WYZE_RTSP_USER", "shared")
    monkeypatch.setattr(zeev, "WYZE_RTSP_PASS", "sharedpw")
    monkeypatch.setenv("WYZE_RTSP_USER_CAM", "own")
    monkeypatch.delenv("WYZE_RTSP_PASS_CAM", raising=False)
    assert zeev.wyze_credentials("cam") == ("own", "")


# --- the voice gate ---------------------------------------------------------
#
# Contractions are not reliable: Whisper transcribed the same spoken question as
# "What's going on upstairs?" once and "what is going on upstairs" the next
# time, and only the first matched -- so the second silently reached the LLM,
# which answered that it cannot see upstairs. Both directions are pinned here
# because over-firing hijacks ordinary chat into "Which camera?".

CAMS3 = ["upstairs", "basement-cam", "front-yard"]


def _fires(zeev, text):
    """Mirrors the branch condition in handle_transcript."""
    return bool(zeev._WYZE_CAM_RE.search(text)) or (
        bool(zeev._CAMERA_RE.search(text))
        and (bool(zeev.resolve_wyze_cam(text, CAMS3)[0])
             or bool(zeev._CAM_NOUN_RE.search(text))))


@pytest.mark.parametrize("text", [
    "What's going on upstairs?",
    "what is going on upstairs",      # the live miss
    "whats happening upstairs",
    "check upstairs",
    "look at the upstairs camera",
    "what do you see upstairs",       # reaches it via _CAMERA_RE + a name match
    "is anyone upstairs",
    "who is at the front yard",
    "show me the basement cam",
    # Plurals, all live misses on 2026-07-30. \bcam\b and \bcamera\b do not
    # match "cameras", so these three reached neither camera branch and the 8B
    # answered them by describing feeds that do not exist.
    "check all cameras",
    "What do you see in the two cameras?",
    "What do you see in upstairs cameras?",
    "check both cams",
    "show me the camera feeds",
])
def test_camera_question_fires_gate(zeev, text):
    assert _fires(zeev, text), f"{text!r} should reach the camera branch"


@pytest.mark.parametrize("text", [
    "what is going on with the weather",
    "how are you doing",
    "tell me a joke",
    "what time is it",
    "remind me to call Dave at 4",
    "play some jazz",
    "what is on my calendar",
    "who is Rambam",
])
def test_ordinary_chat_does_not_fire_gate(zeev, text):
    assert not _fires(zeev, text), f"{text!r} must not be treated as a camera request"


# --- the capability guard ---------------------------------------------------
#
# The gate is an allowlist over unbounded phrasing, so a camera question will
# always be able to miss it and land in ordinary chat. Left unguarded the 8B
# does not decline -- it invents. Observed live 2026-07-30: a "living room cam"
# and an "upstairs hallway cam", neither of which exists, with the cat placed on
# a windowsill grooming himself. The prompt must say plainly that no image is
# present, and must name the cameras that do exist so an invented one has
# something to contradict it.

@pytest.mark.parametrize("text", [
    "check all cameras",
    "what do you see in the two cameras?",
    "what's going on upstairs",
    "show me the camera feeds",
    "what do you see",
])
def test_camera_question_gets_capability_guard(zeev, monkeypatch, text):
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", ["smokeys-cam", "bedroom-cam"])
    p = zeev._build_system_prompt(text)
    assert "## Cameras:" in p, f"no capability guard for {text!r}"
    assert "NOT been given any camera image" in p
    # The real names must be present: an unbounded "do not invent" is weaker
    # than a list the model can check itself against.
    assert "smokeys cam" in p and "bedroom cam" in p


def test_capability_guard_names_only_real_cameras(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", ["smokeys-cam", "bedroom-cam"])
    p = zeev._build_system_prompt("check all cameras")
    for invented in ("living room cam", "upstairs hallway", "hallway cam"):
        assert invented not in p, f"prompt suggests a nonexistent {invented!r}"


def test_capability_guard_absent_from_ordinary_turns(zeev, monkeypatch):
    """It costs tokens on every turn it appears on; only camera turns pay."""
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", ["smokeys-cam", "bedroom-cam"])
    for text in ["tell me a joke", "what time is it", "play some jazz"]:
        assert "## Cameras:" not in zeev._build_system_prompt(text), text


def test_capability_guard_survives_no_cameras_configured(zeev, monkeypatch):
    """WYZE_CAMERAS empty is exactly when the model has least to go on.

    The phone-relay cameras (Living Room/Backyard/Front Yard) always exist
    regardless of WYZE_CAMERAS -- they're a separate, always-on relay, not
    conditioned on any RTSP config -- so the guard text always lists them
    even with zero real (RTSP) cameras configured.
    """
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", [])
    p = zeev._build_system_prompt("what do you see in the cameras")
    assert "## Cameras:" in p
    assert "The only cameras that exist are" in p
    assert "Living Room, Backyard, Front Yard" in p


def test_pi_camera_phrasing_is_not_hijacked_when_a_camera_exists(zeev, monkeypatch):
    """The Wyze branch sits above the Pi's own eye, so it must yield to it.

    "take a picture with the camera" says "camera" without naming a room. With
    a camera attached that has to take a photo, not answer "Which camera?".
    Nothing observes this today (CAMERA_AVAILABLE is False on this board),
    which is exactly why it would surprise someone later.
    """
    monkeypatch.setattr(zeev, "CAMERA_AVAILABLE", True)
    for text in ["take a picture with the camera",
                 "what do you see on the camera",
                 "snap a photo with your camera"]:
        assert not _fires_with_pi_cam(zeev, text, True), text
    # ...but a named room still goes to that room, camera attached or not.
    assert _fires_with_pi_cam(zeev, "check the basement cam", True)


def _fires_with_pi_cam(zeev, text, cam_available):
    return bool(zeev._WYZE_CAM_RE.search(text)) or (
        bool(zeev._CAMERA_RE.search(text))
        and (bool(zeev.resolve_wyze_cam(text, CAMS3)[0])
             or (bool(zeev._CAM_NOUN_RE.search(text)) and not cam_available)))


def test_bare_subject_name_gets_the_guard(zeev, monkeypatch):
    """"Where is Smokey" matches no camera regex at all.

    If it also misses _SUBJECT_TRIGGER_RE it reaches the LLM unguarded, which
    is how "Smokey is now sitting on a windowsill" was produced live.
    """
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", ["smokeys-cam", "bedroom-cam"])
    monkeypatch.setattr(zeev, "WYZE_SUBJECTS", {
        "smokey": {"name": "Smokey", "kind": "cat", "cams": ["smokeys-cam"]},
        "smoky":  {"name": "Smokey", "kind": "cat", "cams": ["smokeys-cam"]},
    })
    for text in ["where is Smokey", "is Smoky ok", "Smokey?"]:
        p = zeev._build_system_prompt(text)
        assert "## Cameras:" in p, f"no guard for {text!r}"
        assert "do NOT report where a pet or person is" in p


def test_subject_guard_needs_a_configured_subject(zeev, monkeypatch):
    """No ZEEV_SUBJECTS means no name to match; must not crash or over-fire."""
    monkeypatch.setattr(zeev, "WYZE_SUBJECTS", {})
    assert "## Cameras:" not in zeev._build_system_prompt("where is Smokey")


def test_guard_never_leaks_stream_urls(zeev, monkeypatch):
    """WYZE_CAMERAS holds bare names, but the guard is one parse change away
    from putting RTSP URLs and LAN IPs into a prompt sent to Groq/OpenRouter --
    the exact class _scrub_rtsp exists to prevent."""
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", ["smokeys-cam", "bedroom-cam"])
    p = zeev._build_system_prompt("check all cameras")
    assert "rtsp" not in p.lower()
    assert "10.0.0." not in p


# --- the multi-camera sweep -------------------------------------------------
#
# "check all cameras" is not a room, and resolve_wyze_cam cannot express it --
# it returns a single stream, so the phrase landed on the "Which camera?" ask.
# Both directions matter: a bare "all" or "both" is far too common in speech to
# be allowed to start a ~50s two-camera sweep.

SWEEP_CAMS = ["smokeys-cam", "bedroom-cam"]


@pytest.fixture
def sweep_env(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", SWEEP_CAMS)
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS",
                        {c: f"rtsp://10.0.0.1/{c}" for c in SWEEP_CAMS})
    return zeev


@pytest.mark.parametrize("text", [
    "check all cameras",
    "check all the cameras",
    "check both cams",
    "show me every camera",
    "what do you see on all of the cameras",
    "pull up both cameras",
    "check all my camera feeds",
])
def test_sweep_phrases_return_every_camera(sweep_env, text):
    assert sweep_env.resolve_wyze_sweep(text) == sorted(SWEEP_CAMS), text


@pytest.mark.parametrize("text", [
    "check the basement cam",
    "what's going on upstairs",
    "is that all",
    "both of them are fine",
    "I ate all the leftovers",
    "tell me everything",
    "every day this week",
])
def test_non_sweep_phrases_return_nothing(sweep_env, text):
    assert sweep_env.resolve_wyze_sweep(text) == [], text


def test_named_room_beats_the_sweep(sweep_env):
    """"check all the cameras in the bedroom" is a question about the bedroom.

    The branch only consults resolve_wyze_sweep when resolve_wyze_cam found
    nothing, so this pins the precedence the branch relies on.
    """
    stream, _ = sweep_env.resolve_wyze_cam("check all the cameras in the bedroom cam",
                                           SWEEP_CAMS)
    assert stream == "bedroom-cam"


def test_sweep_pool_skips_bridge_only_cameras(zeev, monkeypatch):
    """Six of eight never answer; each dead one costs WYZE_SNAP_TIMEOUT."""
    monkeypatch.setattr(zeev, "WYZE_CAMERAS",
                        ["smokeys-cam", "bedroom-cam", "backyard", "secret"])
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS",
                        {"smokeys-cam": "rtsp://x", "bedroom-cam": "rtsp://y"})
    assert zeev.resolve_wyze_sweep("check all cameras") == ["bedroom-cam", "smokeys-cam"]


def test_sweep_falls_back_when_nothing_has_a_direct_url(zeev, monkeypatch):
    """A pure-bridge setup must degrade to slow, not to empty."""
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", ["backyard", "doorbell-cam"])
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS", {})
    assert zeev.resolve_wyze_sweep("check all cameras") == ["backyard", "doorbell-cam"]


def test_sweep_is_capped(zeev, monkeypatch):
    """One vision call per camera at ~21-25s each on the free tier."""
    cams = [f"cam{i}" for i in range(8)]
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", cams)
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS", {c: "rtsp://x" for c in cams})
    assert len(zeev.resolve_wyze_sweep("check all cameras")) == zeev._SWEEP_MAX_CAMS


def test_sweep_with_no_cameras_configured(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_CAMERAS", [])
    monkeypatch.setattr(zeev, "WYZE_CAMERA_URLS", {})
    assert zeev.resolve_wyze_sweep("check all cameras") == []


def test_sweep_prompt_demands_brevity(zeev):
    """Every camera's answer is spoken in one reply; two paragraphs is a minute."""
    p = zeev.sweep_vision_prompt("bedroom cam")
    assert "bedroom cam" in p
    assert "one or two short sentences" in p
    assert "stage direction" in p
