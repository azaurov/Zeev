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
