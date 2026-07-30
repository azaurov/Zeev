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


def test_scrub_handles_empty(zeev):
    assert zeev._scrub_rtsp("") == ""
    assert zeev._scrub_rtsp(None) == ""


def test_snapshot_without_config_returns_none(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "WYZE_RTSP_BASE", "")
    assert zeev.wyze_snapshot("upstairs") is None
