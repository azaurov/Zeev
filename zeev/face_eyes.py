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
  speaking   : neutral brows, mouth is a live waveform driven by eq_levels --
               same real-data-or-synthetic-fallback contract face_aura's
               spokes and face_scroll's equalizer already use: real per-band
               levels when Python has live PCM this turn, a bounded
               synthetic sine when it doesn't (the common Go-daemon-route
               case)
  error      : eyes become X marks, brows angle into a frown/concerned V,
               flat mouth -- same unambiguous-error idiom face_scroll's
               eyes already used

A handful of slow-drifting, twinkling background dots (fixed pseudo-random
positions from a seeded RNG at import time, not re-randomized per frame)
echo the reference's particle backdrop -- purely decorative, cheap (a few
small ellipse fills), no per-frame allocation or resizing, same rendering-
cost discipline as face_aura (this runs on a Pi Zero 2W pushing RGB565 over
SPI at up to 12fps).
"""

import math
import random

from PIL import Image, ImageDraw

from face_scroll import (
    W, H, SEP_Y, _HAT_TOP,
    _STATE_COLORS, _LABELS,
    _font, _FONT_PATH, _FONT_BOLD,
    _draw_battery, _draw_text_area,
)

_EQ_BANDS = 8
_MOUTH_SYNTH_PHASE = [0.31, 0.67, 0.12, 0.88, 0.45, 0.72, 0.20, 0.58]
_mouth_smoothed = [0.0] * _EQ_BANDS  # decay-smoothed waveform points, "speaking" only

_FACE_CY  = _HAT_TOP + 54   # vertical center of the eye row
_EYE_DX   = 32              # horizontal offset of each eye from center
_EYE_R    = 20               # eye outer radius
_MOUTH_Y  = _FACE_CY + _EYE_R + 20

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
    half = 15
    if state == "error":
        # Inner (nose-side) end pulled down, outer end raised -- an angled
        # V that reads as concerned/frowning without needing a mouth change.
        if ex < W // 2:
            draw.line([ex - half, y - 4, ex + half, y + 4], fill=col, width=4)
        else:
            draw.line([ex - half, y + 4, ex + half, y - 4], fill=col, width=4)
        return
    draw.arc([ex - half, y - 6, ex + half, y + 10], start=200, end=340, fill=col, width=4)


def _expr_for_state(state, t):
    """Return (look_x, look_y, brow_dy_left, brow_dy_right)."""
    if state == "thinking":
        sweep = math.sin(t * 1.3) * 5
        return (sweep, -1, -6, 2)
    if state == "listening":
        return (0, 0, -3, -3)
    if state == "error":
        return (0, 1, 0, 0)
    if state == "speaking":
        return (0, 0, 0, 0)
    # idle/ready: slow synchronized bob
    bob = math.sin(t * 0.9) * 1.5
    return (0, 0, bob, bob)


def _mouth_levels(t, eq_levels):
    n = _EQ_BANDS
    if eq_levels:
        raw = [max(0.0, min(1.0, eq_levels[i] if i < len(eq_levels) else 0.0))
               for i in range(n)]
    else:
        raw = [0.18 + 0.30 * math.sin(t * 3.1 + _MOUTH_SYNTH_PHASE[i] * 2 * math.pi) ** 2
               for i in range(n)]
    for i in range(n):
        _mouth_smoothed[i] = max(raw[i], _mouth_smoothed[i] * 0.55)
    return _mouth_smoothed


def _draw_mouth(draw, cx, y, state, t, eq_levels, col):
    if state == "speaking":
        levels = _mouth_levels(t, eq_levels)
        n = len(levels)
        half_w = 32
        seg = (half_w * 2) / (n - 1)
        pts = []
        for i, lvl in enumerate(levels):
            x = cx - half_w + i * seg
            amp = 3 + lvl * 13
            yy = y + amp * math.sin(t * 6 + i * 0.9)
            pts.append((x, yy))
        draw.line(pts, fill=col, width=3, joint="curve")
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
