"""Regression tests for the wake-listener speech gate.

_has_speech() sits in front of the cloud-STT fallback, where every clip that
gets through costs a paid Whisper round trip. Measured on the Pi, webrtcvad
aggressiveness levels 0-2 passed every synthetic signal except digital silence
-- which the RMS gate ahead of it already rejects -- so the gate was a no-op at
the library's usual settings. These tests pin the two properties that make it
worth having at all: it rejects non-voice audio, and it fails open.
"""
import io
import math
import random
import struct
import wave

import pytest

SR = 16000


def _wav(samples, rate=SR):
    raw = struct.pack("<%dh" % len(samples), *samples)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(raw)
    return buf.getvalue()


def _silence(n=SR):
    return [0] * n


def _tone(freq, amp=20000, n=SR):
    return [int(amp * math.sin(2 * math.pi * freq * i / SR)) for i in range(n)]


def _hiss(amp=400, n=SR, seed=1):
    rng = random.Random(seed)
    return [rng.randint(-amp, amp) for _ in range(n)]


@pytest.fixture(autouse=True)
def _reset_vad(zeev):
    """webrtcvad handle is cached in a module-level singleton."""
    zeev._vad_singleton[0] = None
    yield
    zeev._vad_singleton[0] = None


def _requires_vad(zeev):
    try:
        import webrtcvad  # noqa: F401
    except Exception:
        pytest.skip("webrtcvad not installed (needs the webrtcvad-wheels fork on py3.13)")


def test_silence_is_not_speech(zeev):
    _requires_vad(zeev)
    assert zeev._has_speech(_wav(_silence())) is False


def test_pure_tone_is_not_speech(zeev):
    """A loud 1kHz tone clears any RMS gate. It must not reach cloud STT.

    This is the case that regresses if the default aggressiveness is lowered:
    levels 0-2 all classify this as speech.
    """
    _requires_vad(zeev)
    assert zeev._has_speech(_wav(_tone(1000))) is False


def test_low_level_hiss_is_not_speech(zeev):
    _requires_vad(zeev)
    assert zeev._has_speech(_wav(_hiss())) is False


def test_default_aggressiveness_is_strict(zeev):
    """Guards the finding directly: the shipped default must be the strict one."""
    import inspect
    sig = inspect.signature(zeev._has_speech)
    assert sig.parameters["aggressiveness"].default == 3


def test_fails_open_when_webrtcvad_missing(zeev, monkeypatch):
    """A broken/absent VAD must never make the device deaf."""
    import builtins
    real_import = builtins.__import__

    def _no_webrtcvad(name, *a, **kw):
        if name == "webrtcvad":
            raise ImportError("simulated missing webrtcvad")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_webrtcvad)
    zeev._vad_singleton[0] = None
    # Digital silence would otherwise be rejected; with no VAD it must pass.
    assert zeev._has_speech(_wav(_silence())) is True


def test_fails_open_on_malformed_frames(zeev):
    """Odd rates make webrtcvad raise; that must pass audio, not drop it."""
    _requires_vad(zeev)
    assert zeev._has_speech(_wav(_silence(), rate=44100), rate=44100) is True


# --- per-model thresholds ---------------------------------------------------
#
# OWW_THRESHOLD is global, which made two models of unequal quality mutually
# exclusive to tune: measured live on 2026-07-29, sarina's real wakes spanned
# 0.58-0.98 and its false wakes 0.51-0.73 (overlapping, so no global cut helps),
# while zeev fired once in eight hours and that was a false 0.51. Any bump for
# one made the other deaf. These pin the override and the masking rule.

@pytest.fixture
def _thresholds(zeev):
    saved = dict(zeev.OWW_THRESHOLDS)
    yield zeev.OWW_THRESHOLDS
    zeev.OWW_THRESHOLDS.clear()
    zeev.OWW_THRESHOLDS.update(saved)


def test_unlisted_model_keeps_global_threshold(zeev, _thresholds):
    _thresholds["sarina"] = 0.8
    assert zeev.oww_threshold("zeev") == zeev.OWW_THRESHOLD
    assert zeev.oww_threshold("sarina") == 0.8


def test_override_gates_a_score_the_global_would_pass(zeev, _thresholds):
    _thresholds["sarina"] = 0.8
    # 0.73 was a real observed false wake; it clears the 0.5 global.
    assert zeev.oww_best({"sarina": 0.73}) == ("", 0.0)
    assert zeev.oww_best({"sarina": 0.81})[0] == "sarina"


def test_raised_model_does_not_mask_a_quieter_one(zeev, _thresholds):
    """The whole point: thresholding must happen before the max, not after."""
    _thresholds["sarina"] = 0.9
    # sarina is louder but below its own bar; zeev cleared the global 0.5.
    assert zeev.oww_best({"sarina": 0.85, "zeev": 0.60}) == ("zeev", 0.60)


def test_loudest_qualifying_model_wins(zeev, _thresholds):
    assert zeev.oww_best({"sarina": 0.71, "zeev": 0.93}) == ("zeev", 0.93)


def test_no_scores_never_fires(zeev):
    assert zeev.oww_best({}) == ("", 0.0)
    assert zeev.oww_best(None) == ("", 0.0)
