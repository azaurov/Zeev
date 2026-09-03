"""
Eyes-and-eyebrows face for Zeev device mode (240x280 ST7789).

Reference: a round Echo-Spot-style display showing two large glowing eyes
with curved eyebrows, a few drifting background particles, and a mouth that
switches between a calm flat line and a live audio waveform. Adapted here to
Zeev's narrower rectangular panel and existing per-state color language
(`_STATE_COLORS`, shared with face_aura/face_scroll) instead of one fixed
blue. Reuses face_scroll's header row (state dot + label, clock, battery),
scrolling text area, and the draw_frame(state, caption, t, batt=None,
mouth_shape=None, eq_levels=None) signature -- drop-in alternative to
face_aura / face_scroll, same as face_aura was.

Per-state expression:

  idle/ready : soft round eyes, slow blink, brows gently bobbing, flat mouth
  listening  : both brows raised (attentive), flat mouth
  thinking   : one brow raised, pupils drift side to side, three bouncing
               dots for a mouth (a "..." typing indicator)
  speaking   : neutral brows, mouth is one oval that swells with loudness
               and collapses back to a flat line between words. Same
               real-data-or-synthetic-fallback contract face_aura's spokes
               and face_scroll's equalizer use (real levels when Python has
               live PCM this turn, synthetic when it doesn't -- the common
               Go-daemon-route case), but collapsed to a single scalar:
               see _mouth_openness for why per-band driving doesn't work
               for a mouth.
  error      : eyes become X marks, brows angle into a frown/concerned V,
               flat mouth -- same unambiguous-error idiom face_scroll's
               eyes already used

A handful of slow-drifting, twinkling background dots (fixed pseudo-random
positions from a seeded RNG at import time, not re-randomized per frame)
echo the reference's particle backdrop -- purely decorative, cheap (a few
small ellipse fills), no per-frame allocation or resizing, same rendering-
cost discipline as face_aura (this runs on a Pi Zero 2W pushing RGB565 over
SPI at up to 12fps).

Behind the face, one capability glyph at a time drifts across (newspaper,
calendar, camera, ...) -- see the "Capability flyers" section for why the
set is restricted to features with a real code path, and why the mouth had
to become opaque for them to read as depth.
"""

import math
import random
import time

from PIL import Image, ImageDraw

from face_scroll import (
    W, H, SEP_Y, _HAT_TOP,
    _STATE_COLORS, _LABELS,
    _font, _FONT_PATH, _FONT_BOLD,
    _draw_battery, _draw_text_area,
)

_EQ_BANDS = 8
_mouth_open = [0.0]   # smoothed 0-1 openness, "speaking" only (see _mouth_openness)

_FACE_CY  = _HAT_TOP + 54   # vertical center of the eye row
_EYE_DX   = 32              # horizontal offset of each eye from center
_EYE_R    = 20               # eye outer radius
_MOUTH_Y  = _FACE_CY + _EYE_R + 18
# Half-height at full openness. Bounded so a wide-open mouth still clears the
# eyes' outer glow above (~_FACE_CY + _EYE_R + 8) and the separator below.
_BG = (0, 0, 0)          # page background; the mouth fills with this so it occludes
_MOUTH_MAX_OPEN = 14
_MOUTH_HALF_W   = 30

# Openness curve, measured off a real utterance on the device (sampled the
# daemon's own eq_levels via AudioClient.eq_levels() during speak_sync, then
# replayed the trace through this function). The band mean separates cleanly:
# pauses sit at 0.002-0.02, active speech at 0.11-0.36, so a floor just above
# the pause band shuts the mouth outright between words instead of leaving it
# ajar. _MOUTH_FULL is the measured p95 (max was 0.358) -- the previous flat
# x1.6 gain topped out at 0.57 openness, so the mouth never actually opened
# all the way.
_MOUTH_FLOOR   = 0.03
_MOUTH_FULL    = 0.30
_MOUTH_RELEASE = 0.35

_N_PARTICLES = 6
_particle_rng = random.Random(1729)
_PARTICLES = [
    (
        _particle_rng.uniform(10, W - 10),
        _particle_rng.uniform(_HAT_TOP + 4, SEP_Y - 8),
        _particle_rng.uniform(0, 2 * math.pi),
        _particle_rng.uniform(0.4, 1.0),
        _particle_rng.uniform(1.2, 2.4),
    )
    for _ in range(_N_PARTICLES)
]


# ── Capability flyers ────────────────────────────────────────────────────────
# Small glyphs for things Zeev can actually do, drifting across behind the
# face -- the device's features are otherwise undiscoverable (nothing on
# screen ever hints that it can read the parsha, place a call, or check a
# camera).
#
# Every glyph here maps to a real code path (`get_shpeel`, `youtube_play`,
# `gcal_fetch`, `due_reminders`, `vision_complete`, `torah_search`,
# `needs_search`, `_BT_CALL_RE`, `gps_locate`, `needs_weather`,
# `random_joke`). Deliberately NO envelope: zeev.py has no SMTP/IMAP/Gmail
# path of any kind, and a device advertising a capability it doesn't have is
# the same failure as claiming in words to have done something it didn't.
#
# Each takes (draw, cx, cy, s, col) and draws inside a 2s box, axis-aligned
# (no rotation -- PIL would need a per-icon image + paste, and this path
# already needed a perf fix once).

def _ic_newspaper(draw, cx, cy, s, col):
    draw.rectangle([cx - s, cy - s * 0.75, cx + s, cy + s * 0.75], outline=col, width=1)
    draw.line([cx - s + 2, cy - s * 0.75 + 3, cx + s - 2, cy - s * 0.75 + 3], fill=col, width=1)
    for i in range(2):
        yy = cy - s * 0.75 + 7 + i * 4
        draw.line([cx - s + 2, yy, cx + s - 2, yy], fill=col, width=1)


def _ic_music(draw, cx, cy, s, col):
    draw.ellipse([cx - s * 0.9, cy + s * 0.15, cx - s * 0.1, cy + s * 0.85], fill=col)
    draw.line([cx - s * 0.15, cy + s * 0.5, cx - s * 0.15, cy - s * 0.85], fill=col, width=2)
    draw.line([cx - s * 0.15, cy - s * 0.85, cx + s * 0.75, cy - s * 0.5], fill=col, width=2)


def _ic_calendar(draw, cx, cy, s, col):
    draw.rectangle([cx - s * 0.85, cy - s * 0.6, cx + s * 0.85, cy + s * 0.8], outline=col, width=1)
    draw.line([cx - s * 0.85, cy - s * 0.2, cx + s * 0.85, cy - s * 0.2], fill=col, width=1)
    draw.line([cx - s * 0.45, cy - s * 0.9, cx - s * 0.45, cy - s * 0.45], fill=col, width=1)
    draw.line([cx + s * 0.45, cy - s * 0.9, cx + s * 0.45, cy - s * 0.45], fill=col, width=1)
    draw.rectangle([cx - s * 0.5, cy + s * 0.15, cx - s * 0.1, cy + s * 0.5], fill=col)


def _ic_bell(draw, cx, cy, s, col):
    draw.arc([cx - s * 0.7, cy - s * 0.9, cx + s * 0.7, cy + s * 0.5], start=180, end=360,
             fill=col, width=2)
    draw.line([cx - s * 0.7, cy - s * 0.2, cx - s * 0.7, cy + s * 0.3], fill=col, width=1)
    draw.line([cx + s * 0.7, cy - s * 0.2, cx + s * 0.7, cy + s * 0.3], fill=col, width=1)
    draw.line([cx - s * 0.9, cy + s * 0.35, cx + s * 0.9, cy + s * 0.35], fill=col, width=1)
    draw.ellipse([cx - 1.5, cy + s * 0.5, cx + 1.5, cy + s * 0.85], fill=col)


def _ic_camera(draw, cx, cy, s, col):
    draw.rectangle([cx - s * 0.55, cy - s * 0.85, cx - s * 0.1, cy - s * 0.5], outline=col, width=1)
    draw.rounded_rectangle([cx - s * 0.95, cy - s * 0.55, cx + s * 0.95, cy + s * 0.7],
                           radius=2, outline=col, width=1)
    draw.ellipse([cx - s * 0.35, cy - s * 0.25, cx + s * 0.35, cy + s * 0.45],
                 outline=col, width=1)


def _ic_book(draw, cx, cy, s, col):
    draw.rectangle([cx - s * 0.9, cy - s * 0.7, cx + s * 0.9, cy + s * 0.7], outline=col, width=1)
    draw.line([cx, cy - s * 0.7, cx, cy + s * 0.7], fill=col, width=1)
    for i in range(2):
        yy = cy - s * 0.3 + i * 5
        draw.line([cx - s * 0.7, yy, cx - s * 0.2, yy], fill=col, width=1)
        draw.line([cx + s * 0.2, yy, cx + s * 0.7, yy], fill=col, width=1)


def _ic_search(draw, cx, cy, s, col):
    draw.ellipse([cx - s * 0.9, cy - s * 0.9, cx + s * 0.3, cy + s * 0.3], outline=col, width=2)
    draw.line([cx + s * 0.2, cy + s * 0.2, cx + s * 0.9, cy + s * 0.9], fill=col, width=2)


def _ic_phone(draw, cx, cy, s, col):
    draw.rounded_rectangle([cx - s * 0.5, cy - s * 0.9, cx + s * 0.5, cy + s * 0.9],
                           radius=2, outline=col, width=1)
    draw.line([cx - s * 0.2, cy - s * 0.6, cx + s * 0.2, cy - s * 0.6], fill=col, width=1)
    draw.ellipse([cx - 1.2, cy + s * 0.5, cx + 1.2, cy + s * 0.75], fill=col)


def _ic_pin(draw, cx, cy, s, col):
    draw.ellipse([cx - s * 0.6, cy - s * 0.9, cx + s * 0.6, cy + s * 0.3], outline=col, width=2)
    draw.line([cx - s * 0.4, cy + s * 0.1, cx, cy + s * 0.9], fill=col, width=2)
    draw.line([cx + s * 0.4, cy + s * 0.1, cx, cy + s * 0.9], fill=col, width=2)


def _ic_cloud(draw, cx, cy, s, col):
    draw.ellipse([cx - s * 0.95, cy - s * 0.1, cx - s * 0.05, cy + s * 0.6], fill=col)
    draw.ellipse([cx - s * 0.5, cy - s * 0.6, cx + s * 0.5, cy + s * 0.5], fill=col)
    draw.ellipse([cx + s * 0.05, cy - s * 0.15, cx + s * 0.95, cy + s * 0.6], fill=col)


def _ic_smile(draw, cx, cy, s, col):
    draw.ellipse([cx - s * 0.85, cy - s * 0.85, cx + s * 0.85, cy + s * 0.85],
                 outline=col, width=1)
    draw.ellipse([cx - s * 0.45, cy - s * 0.4, cx - s * 0.2, cy - s * 0.1], fill=col)
    draw.ellipse([cx + s * 0.2, cy - s * 0.4, cx + s * 0.45, cy - s * 0.1], fill=col)
    draw.arc([cx - s * 0.5, cy - s * 0.3, cx + s * 0.5, cy + s * 0.6], start=20, end=160,
             fill=col, width=1)


_FLYERS = (
    _ic_newspaper,   # get_shpeel / world news
    _ic_calendar,    # gcal_fetch
    _ic_music,       # youtube_play
    _ic_bell,        # reminders / timers
    _ic_camera,      # vision_complete / Wyze
    _ic_book,        # torah_search
    _ic_search,      # needs_search / Tavily
    _ic_phone,       # BT HFP calls
    _ic_pin,         # gps_locate
    _ic_cloud,       # needs_weather
    _ic_smile,       # random_joke
)

_FLY_SIZE   = 9      # half-size, so each glyph is ~18px
_FLY_PERIOD = 6.5    # seconds from one glyph's entry to the next
_FLY_TRAVEL = 4.5    # seconds to cross, leaving a gap of empty screen between
_FLY_DIM    = 0.42   # background weight -- must not compete with the face
# Lanes deliberately avoid the vertical middle of the eye row: a glyph is
# occluded by the eyes anyway (it's drawn first), but one crossing exactly
# through both pupils reads as damage rather than depth.
_FLY_LANES  = (_HAT_TOP + 14, SEP_Y - 14, _HAT_TOP + 26, SEP_Y - 26)


_CAP_GLYPHS = {
    "news":     _ic_newspaper,
    "calendar": _ic_calendar,
    "music":    _ic_music,
    "reminder": _ic_bell,
    "camera":   _ic_camera,
    "torah":    _ic_book,
    "search":   _ic_search,
    "call":     _ic_phone,
    "gps":      _ic_pin,
    "weather":  _ic_cloud,
    "joke":     _ic_smile,
}

# How long a noted capability stays on screen. Expiry is by timestamp rather
# than an explicit clear: the gate branches in zeev.py have many exit paths
# (early return, exception, backgrounded thread), and a badge that could stick
# forever would eventually be lying about what the device is doing. Failing
# toward silence is the same default gps_summary's accuracy gate uses.
_CAP_HOLD    = 10.0
_CAP_BADGE_Y = _FACE_CY - _EYE_R - 14   # the clear gap between the two brows
_active_cap  = [None, 0.0]              # (name, time.time() when noted)


def note_capability(name):
    """Record that `name` is actually running right now.

    Called from zeev.py's gate branches (see its note_capability wrapper).
    Unknown names are ignored rather than raising -- this is decoration on a
    live turn and must never be able to take one down.
    """
    if name in _CAP_GLYPHS:
        _active_cap[0] = name
        _active_cap[1] = time.time()


def _active_capability(t):
    """Name of the capability to badge right now, or None."""
    name, at = _active_cap
    if not name:
        return None
    age = t - at
    if age < 0 or age > _CAP_HOLD:
        return None
    return name


def _draw_capability_badge(draw, t, col, name):
    """The running capability, pinned above the eyes and pulsing.

    Full-strength rather than dimmed like the ambient flyers: this one means
    something specific is happening, and it has to be told apart from the
    decorative drift at a glance. Drawn last so nothing occludes it, in the
    one band of the header that no face element uses.
    """
    pulse = 0.72 + 0.28 * math.sin(t * 3.4)
    fade  = min(1.0, max(0.0, (_CAP_HOLD - (t - _active_cap[1])) / 1.2))
    shade = tuple(max(0, min(255, int(c * pulse * fade))) for c in col)
    if max(shade) < 12:
        return
    _CAP_GLYPHS[name](draw, W // 2, _CAP_BADGE_Y, _FLY_SIZE, shade)


def _draw_flyers(draw, t, col, state):
    """One capability glyph drifting across, behind the face.

    Position is a pure function of `t` rather than an accumulated per-frame
    step: _FACE_INTERVAL renders each state at a different rate, so anything
    incremental would drift at a different speed per state.

    Skipped in "ready"/"error", which render at 1fps -- a glyph would jump
    ~50px per frame there and read as a glitch rather than motion.
    """
    if state in ("ready", "error"):
        return
    slot  = int(t / _FLY_PERIOD)
    phase = (t % _FLY_PERIOD) / _FLY_TRAVEL
    if phase > 1.0:
        return
    # Fade in and out at the edges so glyphs don't pop into existence.
    edge = min(1.0, phase * 6.0, (1.0 - phase) * 6.0)
    shade = tuple(max(0, min(255, int(c * _FLY_DIM * edge))) for c in col)
    if max(shade) < 12:
        return
    x = -_FLY_SIZE * 2 + phase * (W + _FLY_SIZE * 4)
    y = _FLY_LANES[slot % len(_FLY_LANES)]
    _FLYERS[slot % len(_FLYERS)](draw, x, y, _FLY_SIZE, shade)


def _draw_particles(draw, t, col):
    for x, y, phase, speed, r in _PARTICLES:
        yy = y + 3 * math.sin(t * speed + phase)
        twinkle = 0.35 + 0.5 * (0.5 + 0.5 * math.sin(t * speed * 1.8 + phase))
        shade = tuple(max(0, min(255, int(c * twinkle))) for c in col)
        draw.ellipse([x - r, yy - r, x + r, yy + r], fill=shade)


def _draw_eye(draw, ex, ey, r, col, blink, look_x=0.0, look_y=0.0):
    if blink:
        draw.line([ex - r, ey, ex + r, ey], fill=col, width=4)
        return
    for k in (2, 1):
        rr = r + k * 4
        shade = tuple(max(0, c - 55 * k) for c in col)
        draw.ellipse([ex - rr, ey - rr, ex + rr, ey + rr], outline=shade, width=2)
    draw.ellipse([ex - r, ey - r, ex + r, ey + r], fill=(8, 11, 20), outline=col, width=3)
    ir = int(r * 0.60)
    ix, iy = ex + look_x, ey + look_y
    draw.ellipse([ix - ir, iy - ir, ix + ir, iy + ir], fill=col)
    pr = max(2, int(r * 0.30))
    draw.ellipse([ix - pr, iy - pr, ix + pr, iy + pr], fill=(6, 8, 14))
    hr = max(2, int(r * 0.16))
    hx, hy = ix - pr * 0.6, iy - pr * 0.6
    draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=(255, 255, 255))


def _draw_brow(draw, ex, y, col, state):
    half = 17
    if state == "error":
        # Inner (nose-side) end pulled down, outer end raised -- an angled
        # V that reads as concerned/frowning without needing a mouth change.
        if ex < W // 2:
            draw.line([ex - half, y - 5, ex + half, y + 5], fill=col, width=4)
        else:
            draw.line([ex - half, y + 5, ex + half, y - 5], fill=col, width=4)
        return
    draw.arc([ex - half, y - 9, ex + half, y + 13], start=200, end=340, fill=col, width=4)


def _expr_for_state(state, t):
    """Return (look_x, look_y, brow_dy_left, brow_dy_right)."""
    if state == "thinking":
        sweep = math.sin(t * 1.3) * 5
        return (sweep, -1, -10, 5)
    if state == "listening":
        return (0, 0, -6, -6)
    if state == "error":
        return (0, 1, 0, 0)
    if state == "speaking":
        return (0, 0, 0, 0)
    # idle/ready: slow synchronized bob
    bob = math.sin(t * 0.9) * 2.5
    return (0, 0, bob, bob)


def _mouth_openness(t, eq_levels):
    """One scalar 0-1 for how far the mouth is open this frame.

    Deliberately a single value rather than the 8 independent per-band
    levels the spokes/equalizer use: a mouth has to move as one cohesive
    shape, and driving each x-position separately reads as a scattered
    squiggle rather than a mouth (that was the first version of this).
    """
    if eq_levels:
        vals = [max(0.0, min(1.0, v)) for v in eq_levels[:_EQ_BANDS]]
        # Mean, not max: one loud band shouldn't hold the mouth wide open
        # through a pause. Floor-and-rescale rather than a plain gain --
        # both constants are measured off a real utterance on the device,
        # see _MOUTH_FLOOR/_MOUTH_FULL.
        mean = (sum(vals) / len(vals)) if vals else 0.0
        raw = (mean - _MOUTH_FLOOR) / (_MOUTH_FULL - _MOUTH_FLOOR)
        raw = max(0.0, min(1.0, raw))
    else:
        # No live PCM this turn (the Go-daemon route hands none back) --
        # synthesize a syllable rhythm. A plain sine never reaches zero, so
        # it would never form the closing line; this shapes it to actually
        # shut between "words".
        raw = max(0.0, math.sin(t * 5.0)) ** 1.5
        if (t % 2.6) > 2.1:
            raw = 0.0
    prev = _mouth_open[0]
    # Fast attack, quick-but-not-instant release. The release constant is
    # load-bearing: at 0.55 the mouth was still 4px open a frame after the
    # level had already dropped into the pause band, which is exactly the
    # "stays open through pauses" symptom. At _MOUTH_RELEASE it reaches the
    # flat line within ~2 frames (~0.25s at the 8fps speaking render rate),
    # short enough to shut between words but not so abrupt that it strobes.
    _mouth_open[0] = raw if raw > prev else prev * _MOUTH_RELEASE + raw * (1 - _MOUTH_RELEASE)
    return _mouth_open[0]


def _draw_mouth(draw, cx, y, state, t, eq_levels, col):
    if state == "speaking":
        # One oval that swells with loudness and collapses back to a flat
        # line between words -- narrower as it opens, so a wide-open mouth
        # reads as round rather than as a stretched slot.
        o = _mouth_openness(t, eq_levels)
        h = o * _MOUTH_MAX_OPEN
        w = _MOUTH_HALF_W * (1.0 - 0.25 * o)
        if h < 2.0:
            draw.line([cx - w, y, cx + w, y], fill=col, width=3)
        else:
            # Filled with the page background, not left hollow: the capability
            # glyphs drift behind the face, and the eyes occlude them cleanly
            # only because they're filled. An outline-only mouth let a glyph
            # show through its middle, which read as damage rather than depth.
            draw.ellipse([cx - w, y - h, cx + w, y + h], fill=_BG, outline=col, width=3)
        return
    if state == "thinking":
        for i in range(3):
            dx = (i - 1) * 12
            bounce = abs(math.sin(t * 3 + i * 0.6)) * 4
            r = 3
            draw.ellipse([cx + dx - r, y - bounce - r, cx + dx + r, y - bounce + r], fill=col)
        return
    if state == "error":
        draw.line([cx - 14, y, cx + 14, y], fill=col, width=3)
        return
    if state == "listening":
        draw.arc([cx - 14, y - 6, cx + 14, y + 4], start=15, end=165, fill=col, width=3)
        return
    # idle/ready: calm flat line
    draw.line([cx - 14, y, cx + 14, y], fill=col, width=3)


def _draw_face(draw, t, col, state, eq_levels=None):
    cx, cy = W // 2, _FACE_CY
    blink = (t % 4.0) < 0.15

    _draw_particles(draw, t, col)
    # The ambient drift stands down while a real capability is running -- two
    # moving signals at once reads as noise, and only one of them means
    # anything.
    cap = _active_capability(t)
    if cap is None:
        _draw_flyers(draw, t, col, state)

    look_x, look_y, brow_dy_l, brow_dy_r = _expr_for_state(state, t)

    for side, dx in ((0, -_EYE_DX), (1, _EYE_DX)):
        ex = cx + dx
        if state == "error" and not blink:
            r = _EYE_R * 0.55
            draw.line([ex - r, cy - r, ex + r, cy + r], fill=col, width=4)
            draw.line([ex - r, cy + r, ex + r, cy - r], fill=col, width=4)
        else:
            _draw_eye(draw, ex, cy, _EYE_R, col, blink, look_x, look_y)

        brow_dy = brow_dy_l if side == 0 else brow_dy_r
        _draw_brow(draw, ex, cy - _EYE_R - 14 + brow_dy, col, state)

    _draw_mouth(draw, cx, _MOUTH_Y, state, t, eq_levels, col)

    if cap is not None:
        _draw_capability_badge(draw, t, col, cap)


# ── Header (state dot + label, clock, battery, face, separator) ──────────────

def _draw_header(img, draw, state, col, batt=None, t=0.0, eq_levels=None):
    label = _LABELS.get(state, state)
    from datetime import datetime
    clock = datetime.now().strftime("%H:%M")

    font_sm  = _font(_FONT_PATH, 13)
    font_lbl = _font(_FONT_BOLD, 14)

    dot_r = 5
    draw.ellipse([8, 6, 8 + dot_r * 2, 6 + dot_r * 2], fill=col)
    draw.text((22, 4), label, font=font_lbl, fill=col)

    bb = draw.textbbox((0, 0), clock, font=font_sm)
    clock_w = bb[2] - bb[0]

    if batt is not None:
        level, charging = batt
        batt_x = W - 22 - 3 - 3 - 8
        draw.text((batt_x - clock_w - 6, 5), clock, font=font_sm, fill=(130, 140, 170))
        _draw_battery(draw, batt_x, 5, int(level) if level is not None else None, charging)
        if level is not None:
            batt_col = (80, 220, 120) if charging else \
                       (220, 60, 60) if level < 20 else \
                       (220, 180, 40) if level < 50 else (100, 130, 170)
            font_tiny = _font(_FONT_PATH, 10)
            pct = f"{int(level)}%"
            pct_w = draw.textbbox((0, 0), pct, font=font_tiny)[2]
            draw.text((batt_x + (22 + 3 - pct_w) // 2, 18), pct,
                      font=font_tiny, fill=batt_col)
    else:
        draw.text((W - clock_w - 8, 5), clock, font=font_sm, fill=(130, 140, 170))

    _draw_face(draw, t, col, state, eq_levels=eq_levels)

    draw.line([0, SEP_Y - 1, W, SEP_Y - 1], fill=(40, 44, 55), width=1)


# ── Public API ────────────────────────────────────────────────────────────────

def draw_frame(state: str, caption: str, t: float, batt=None, mouth_shape=None,
               eq_levels=None) -> Image.Image:
    """
    Render one 240x280 frame. Same signature as face_scroll.draw_frame --
    mouth_shape is accepted and ignored (lipsync shapes don't apply here;
    kept so this is a true drop-in for the _face_loop() call site).
    """
    img  = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    col  = _STATE_COLORS.get(state, (120, 120, 120))

    _draw_header(img, draw, state, col, batt=batt, t=t, eq_levels=eq_levels)
    _draw_text_area(img, draw, caption, t)

    return img
