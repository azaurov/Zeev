"""Zeev and Sarina howling like wolves.

Synthesised (numpy), not TTS and not a committed asset: Kokoro/Orpheus read
"Awooo" as text, and zeev/data/ is git-ignored so a shipped WAV would never
reach the Pi. Asserts the audio really exists and Sarina really sits higher --
"returned a path" would pass a silent file.
"""
import inspect
import os
import wave

import pytest

np = pytest.importorskip("numpy")


@pytest.mark.parametrize("text", [
    "howl like a wolf",
    "can you both howl",
    "do a wolf howl",
    "Zeev, Sarina, howl for me",
    "awooo",
])
def test_requests_reach_the_howl(zeev, text):
    assert zeev.howl_intent(text)


@pytest.mark.parametrize("text", [
    "remind me to howl at six",             # a reminder
    "why does my dog howl at night",        # a question about howling
    "how do wolves howl",
    "what does a wolf howl sound like",
    "don't cry wolf",                       # "wolf" alone is ordinary speech
    "the wind was howling",                 # not a request
    "play some jazz",
])
def test_other_intents_are_left_alone(zeev, text):
    assert not zeev.howl_intent(text)


def _read(path):
    with wave.open(path) as w:
        sr, n = w.getframerate(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(float)
    return sr, x


def _peak_hz(x, sr):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / sr)
    return freqs[int(np.argmax(spec[freqs > 80])) + int(np.sum(freqs <= 80))]


def test_render_is_audible_and_the_expected_length(zeev):
    path = zeev.render_howl()
    try:
        sr, x = _read(path)
    finally:
        os.unlink(path)
    assert sr == zeev._DUET_SR
    v = zeev._HOWL_STYLES["classic"]["voices"]
    tone = zeev._HOWL_STYLES["classic"]["tone"]
    expect = v["zeev"][3] + v["sarina"][3] + tone[10] + 2 * 0.35
    assert len(x) / sr == pytest.approx(expect, abs=0.05)
    assert np.max(np.abs(x)) > 0.5 * 32767, "non-silent, normalised"
    assert np.sqrt(np.mean(x ** 2)) > 1500, "sustained tone, not a click"


def test_sarina_howls_higher_than_zeev(zeev):
    path = zeev.render_howl()
    try:
        sr, x = _read(path)
    finally:
        os.unlink(path)
    v = zeev._HOWL_STYLES["classic"]["voices"]
    zs, ss = int(v["zeev"][3] * sr), int(v["sarina"][3] * sr)
    gap = int(0.35 * sr)
    z_seg = x[:zs]
    s_seg = x[zs + gap:zs + gap + ss]
    assert _peak_hz(s_seg, sr) > _peak_hz(z_seg, sr) * 1.2


def test_creepy_style_is_kept_but_not_the_default(zeev):
    """Saved for later: rendering works, nothing selects it yet."""
    assert zeev._HOWL_DEFAULT_STYLE == "classic"
    path = zeev.render_howl("creepy")
    try:
        sr, x = _read(path)
    finally:
        os.unlink(path)
    assert np.max(np.abs(x)) > 0.5 * 32767
    assert len(x) / sr > 12


def test_gate_is_wired_into_the_turn_handler(zeev):
    """Structural: the branch must actually play the file, clean it up, and file
    the turn without re-speaking it (speak=False)."""
    src = inspect.getsource(zeev._handle_transcript)
    i = src.index("howl_intent(transcript)")
    block = src[i:i + 1800]
    assert "render_howl()" in block
    assert "play_duet_wav(" in block
    assert "os.unlink(wav)" in block
    assert "speak=False" in block
