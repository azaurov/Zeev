"""The face_eyes mouth: opens as an oval, shuts to a line between words.

These constants are not taste -- they were measured off a real utterance on
the device (sampled the daemon's own eq_levels via AudioClient.eq_levels()
during a speak_sync, then replayed the trace through _mouth_openness). The
band mean separates cleanly: pauses sit at 0.002-0.02, active speech at
0.11-0.36. Two regressions this pins, both of which shipped once:

  - a flat x1.6 gain, which topped out at 0.57 openness so the mouth never
    actually opened all the way;
  - too slow a release, which left the mouth ~4px ajar a frame after the
    level had already dropped into the pause band -- read on the device as
    "the mouth stays open through pauses".
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "zeev"))

face_eyes = pytest.importorskip("face_eyes")


@pytest.fixture(autouse=True)
def reset_mouth():
    face_eyes._mouth_open[0] = 0.0
    yield
    face_eyes._mouth_open[0] = 0.0


def _openness(levels, frames=1, t0=100.0):
    """Drive the smoother for `frames` frames at the 8fps speaking rate."""
    o = 0.0
    for i in range(frames):
        o = face_eyes._mouth_openness(t0 + i / 8, levels)
    return o


def _bands(mean):
    """8 flat bands whose mean is `mean` -- shape the real eq_levels have."""
    return [mean] * face_eyes._EQ_BANDS


def test_pause_band_levels_shut_the_mouth_completely():
    # Measured pause values (p05-p70 of a real utterance sat at 0.0008-0.0021).
    for mean in (0.0008, 0.0021, 0.02):
        face_eyes._mouth_open[0] = 0.0
        o = _openness(_bands(mean))
        assert o == 0.0, f"pause level {mean} left the mouth open ({o})"


def test_loud_speech_opens_the_mouth_fully():
    # Measured p95 was 0.2965 (just under _MOUTH_FULL, so ~0.99) and the
    # observed max 0.358 clamps to a fully open oval. The point of the floor
    # /rescale is that real speech reaches the top of the range at all -- the
    # previous flat x1.6 gain capped this at 0.57.
    assert _openness(_bands(0.2965)) >= 0.95
    assert _openness(_bands(0.358)) == pytest.approx(1.0, abs=0.01)


def test_mouth_renders_a_line_when_shut_and_an_oval_when_open():
    """h < 2px is the flat-line cutoff in _draw_mouth -- the visible contract."""
    shut = _openness(_bands(0.0021)) * face_eyes._MOUTH_MAX_OPEN
    open_ = _openness(_bands(0.30)) * face_eyes._MOUTH_MAX_OPEN
    assert shut < 2.0
    assert open_ >= 2.0


def test_release_reaches_the_line_within_two_frames_of_a_pause():
    """The "stays open through pauses" regression, pinned.

    Full open, then the level drops into the pause band: the mouth must be
    back under the flat-line cutoff within two 8fps frames (~0.25s), which is
    shorter than a real inter-word pause.
    """
    _openness(_bands(0.358))                      # drive to fully open
    assert face_eyes._mouth_open[0] == pytest.approx(1.0, abs=0.01)
    o = _openness(_bands(0.0021), frames=2)
    assert o * face_eyes._MOUTH_MAX_OPEN < 2.0, (
        f"still {o * face_eyes._MOUTH_MAX_OPEN:.1f}px open two frames into a pause"
    )


def test_attack_is_instant_so_a_syllable_is_not_missed():
    """Rise is immediate; only the release is smoothed."""
    face_eyes._mouth_open[0] = 0.0
    assert _openness(_bands(0.30), frames=1) == pytest.approx(1.0, abs=0.01)


def test_one_loud_band_alone_does_not_open_the_mouth_wide():
    """Mean, not max: a single hot band is damped, not ignored.

    Under a max rule this reads as 1.0 (mouth wide open on one band); under
    the mean it lands near 0.35. It is deliberately NOT zero -- one band at
    full scale puts the mean at 0.125, inside the measured speech range, and
    nothing in 8 pooled bands can distinguish that from genuinely quiet
    speech. Damping is the guarantee here, not suppression.
    """
    levels = [0.0] * face_eyes._EQ_BANDS
    levels[0] = 1.0
    assert _openness(levels) < 0.5


def test_synthetic_fallback_still_cycles_open_and_shut():
    """No live PCM (the Go-daemon route) must still open AND close."""
    seen_open = seen_shut = False
    for i in range(80):
        o = face_eyes._mouth_openness(100.0 + i / 8, None)
        h = o * face_eyes._MOUTH_MAX_OPEN
        seen_open |= h >= 2.0
        seen_shut |= h < 2.0
    assert seen_open and seen_shut


# ── Capability flyers ────────────────────────────────────────────────────────

def _flyer_drawn(state, t):
    """True if a flyer put anything on an otherwise empty frame.

    getbbox() rather than getdata(): it's the non-zero bounding box, so it
    answers "did anything get drawn" directly and without the pixel-sequence
    deprecation.
    """
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (face_eyes.W, face_eyes.SEP_Y), (0, 0, 0))
    face_eyes._draw_flyers(ImageDraw.Draw(img), t, (0, 210, 230), state)
    return img.getbbox() is not None


def test_every_flyer_glyph_renders():
    from PIL import Image, ImageDraw
    for fn in face_eyes._FLYERS:
        img = Image.new("RGB", (40, 40), (0, 0, 0))
        fn(ImageDraw.Draw(img), 20, 20, face_eyes._FLY_SIZE, (0, 210, 230))
        assert img.getbbox() is not None, f"{fn.__name__} drew nothing"


def test_flyers_are_suppressed_in_one_fps_states():
    """ready/error render at 1fps (_FACE_INTERVAL) -- a glyph would jump ~50px
    per frame and read as a glitch rather than as motion."""
    t = 2.25   # mid-crossing, so a glyph would definitely be drawn otherwise
    assert _flyer_drawn("listening", t)
    for state in ("ready", "error"):
        assert not _flyer_drawn(state, t)


def test_flyers_leave_gaps_between_glyphs():
    """_FLY_TRAVEL < _FLY_PERIOD, so the screen is clear between crossings."""
    assert face_eyes._FLY_TRAVEL < face_eyes._FLY_PERIOD
    quiet = face_eyes._FLY_PERIOD - 0.1     # after this crossing has finished
    assert not _flyer_drawn("listening", quiet)


def test_flyer_set_advertises_no_capability_zeev_lacks():
    """The device must not advertise what it cannot do -- notably email, which
    has no SMTP/IMAP/Gmail path anywhere in zeev.py."""
    names = {fn.__name__ for fn in face_eyes._FLYERS}
    assert "_ic_envelope" not in names and "_ic_mail" not in names


def test_open_mouth_occludes_rather_than_tangles_with_a_glyph():
    """The mouth is filled with the background so glyphs pass behind it.

    Left hollow, a glyph showed through the mouth's middle and read as
    damage. Pinned by drawing a glyph dead centre under a wide-open mouth and
    checking the mouth's interior is background, not glyph.
    """
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (face_eyes.W, face_eyes.SEP_Y), (0, 0, 0))
    d = ImageDraw.Draw(img)
    col = (0, 210, 230)
    # Glyph straight through where the mouth will be drawn.
    face_eyes._ic_newspaper(d, face_eyes.W // 2, face_eyes._MOUTH_Y,
                            face_eyes._FLY_SIZE, col)
    face_eyes._mouth_open[0] = 1.0
    face_eyes._draw_mouth(d, face_eyes.W // 2, face_eyes._MOUTH_Y, "speaking",
                          0.0, [0.36] * face_eyes._EQ_BANDS, col)
    assert img.getpixel((face_eyes.W // 2, face_eyes._MOUTH_Y)) == (0, 0, 0)
