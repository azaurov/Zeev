"""Wake-triggered ambient webcam capture.

Two failure modes matter here, the same shape as the Wyze camera-capability
guard: firing a fresh capture on every single wake word would multiply spend
on a shared free-tier vision endpoint for a background nicety, and injecting
a stale description into a turn's prompt would have Zeev describe the room
as it looked minutes ago as though it were the room right now.
"""
import time

import pytest


@pytest.fixture(autouse=True)
def _reset_ambient_state(zeev):
    """Each test gets a clean cache and no in-flight capture."""
    zeev._ambient_vision.update(
        {"desc": None, "desc_ts": 0.0, "attempt_ts": 0.0, "capturing": False})
    yield
    zeev._ambient_vision.update(
        {"desc": None, "desc_ts": 0.0, "attempt_ts": 0.0, "capturing": False})


def test_no_capture_without_a_camera(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "CAMERA_AVAILABLE", False)
    called = []
    monkeypatch.setattr(zeev, "capture_image", lambda *a, **k: called.append(1))
    zeev._start_ambient_capture()
    time.sleep(0.05)
    assert not called


def test_capture_throttled_to_min_interval(zeev, monkeypatch):
    """A capture attempted moments ago must not fire again immediately --
    this is what stops a busy room (repeated wake words) from hammering the
    shared free-tier vision endpoint."""
    monkeypatch.setattr(zeev, "CAMERA_AVAILABLE", True)
    zeev._ambient_vision["attempt_ts"] = time.time()
    called = []
    monkeypatch.setattr(zeev, "capture_image", lambda *a, **k: called.append(1) or None)
    zeev._start_ambient_capture()
    time.sleep(0.05)
    assert not called


def test_capture_fires_when_stale(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "CAMERA_AVAILABLE", True)
    zeev._ambient_vision["attempt_ts"] = time.time() - zeev._AMBIENT_VISION_MIN_INTERVAL_S - 1
    monkeypatch.setattr(zeev, "capture_image", lambda *a, **k: "fake_b64")
    monkeypatch.setattr(zeev, "vision_complete", lambda *a, **k: ("A tidy desk near a window.", None))
    zeev._start_ambient_capture()
    time.sleep(0.2)
    assert zeev._ambient_vision["desc"] == "A tidy desk near a window."
    assert not zeev._ambient_vision["capturing"]


def test_failed_capture_still_updates_attempt_ts(zeev, monkeypatch):
    """A vision-API outage must not turn into a retry-on-every-wake loop --
    the attempt timestamp is set before the network call, not after success."""
    monkeypatch.setattr(zeev, "CAMERA_AVAILABLE", True)
    zeev._ambient_vision["attempt_ts"] = time.time() - zeev._AMBIENT_VISION_MIN_INTERVAL_S - 1
    monkeypatch.setattr(zeev, "capture_image", lambda *a, **k: None)
    zeev._start_ambient_capture()
    time.sleep(0.05)
    assert zeev._ambient_vision["desc"] is None
    assert zeev._ambient_vision["attempt_ts"] > 0
    # Immediately retrying must now be throttled.
    called = []
    monkeypatch.setattr(zeev, "capture_image", lambda *a, **k: called.append(1) or None)
    zeev._start_ambient_capture()
    time.sleep(0.05)
    assert not called


def test_fresh_description_is_returned(zeev):
    zeev._ambient_vision["desc"] = "A man at a desk."
    zeev._ambient_vision["desc_ts"] = time.time()
    assert zeev._fresh_ambient_vision() == "A man at a desk."


def test_stale_description_is_not_returned(zeev):
    """The core staleness guard: a description from minutes ago must never be
    read out as what Zeev sees right now."""
    zeev._ambient_vision["desc"] = "A man at a desk."
    zeev._ambient_vision["desc_ts"] = time.time() - zeev._AMBIENT_VISION_FRESH_S - 1
    assert zeev._fresh_ambient_vision() is None


def test_no_description_returns_none(zeev):
    assert zeev._fresh_ambient_vision() is None
