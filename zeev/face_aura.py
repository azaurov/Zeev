"""
Reactive-aura face for Zeev device mode (240x280 ST7789).

Replaces the Miss Minutes character (idle/ready/listening/thinking/error)
and the separate 8-bar equalizer (speaking) with one continuous visual
language: a soft pulsing orb with 8 radiating spokes. Color and motion
follow `_STATE_COLORS` same as face_scroll; the header layout (state dot +
label, clock, battery, separator, auto-scrolling reply text) is unchanged
and reused directly from face_scroll so this is a drop-in replacement with
the same draw_frame(state, caption, t, batt=None, mouth_shape=None,
eq_levels=None) signature.

Per-state motion (all driven by the same 8-value `levels` array that
`_draw_equalizer` already used only for "speaking" -- extended here to
every state so the orb is never static):

  idle/ready : slow uniform low-amplitude breathing, all 8 spokes together
  listening  : gentle irregular murmur, slightly higher energy than idle
  thinking   : single bright spoke sweeps around the ring (radar-style)
  speaking   : real per-band audio levels (or synthetic fallback) --
               same eq_levels contract face_scroll._draw_equalizer used,
               just rendered as radiating spokes instead of bar-graph bars
  error      : all 8 spokes strobe together, orb jitters side to side

Rendering cost is deliberately kept in the same ballpark as the code it
replaces (a handful of ellipse fills + short lines per frame, no
per-frame image resizing or alpha compositing) -- this runs on a Pi Zero
2W pushing RGB565 over SPI at up to 12fps, and that path has already
needed a performance fix once (see docs/whisplay-device-mode.md).
"""

import math

from PIL import Image, ImageDraw

from face_scroll import (
    W, H, SEP_Y, _HAT_TOP,
    _STATE_COLORS, _LABELS,
    _font, _FONT_PATH, _FONT_BOLD,
    _draw_battery, _draw_text_area,
)

_EQ_BANDS = 8
_IDLE_PHASE = [0.31, 0.67, 0.12, 0.88, 0.45, 0.72, 0.20, 0.58]
_aura_smoothed = [0.0] * _EQ_BANDS  # decay-smoothed per-spoke levels, all states

_AURA_CX_Y = _HAT_TOP + 50   # orb center, roughly where the old face sat
_BASE_R    = 18             # resting core radius
_MOD_R     = 12              # extra radius at full amplitude
_MAX_SPOKE = 32              # extra spoke length at full amplitude
_GLOW_LAYERS = 4


def _band_levels(state, t, eq_levels):
    """Return 8 values (0-1), one per spoke, for the given state/time.

    "speaking" reuses face_scroll._draw_equalizer's exact contract: real
    per-band levels when Python has live PCM (Orpheus/BT-fallback route),
    or a bounded per-band-phased sine when it doesn't (Go daemon route,
    the common case) -- same real-or-synthetic fallback, just rendered as
    spokes instead of bars.
    """
    n = _EQ_BANDS
    if state == "speaking":
        if eq_levels:
            return [max(0.0, min(1.0, eq_levels[i] if i < len(eq_levels) else 0.0))
                    for i in range(n)]
        return [0.18 + 0.30 * math.sin(t * 3.1 + _IDLE_PHASE[i] * 2 * math.pi) ** 2
                for i in range(n)]
    if state == "thinking":
        # Single bright spoke sweeps the ring -- reads as "working on it"
        # without claiming to reflect anything real, same honesty as the
        # synthetic speaking fallback above.
        active = int(t * 1.6) % n
        return [0.85 if i == active else 0.08 for i in range(n)]
    if state == "listening":
        return [0.14 + 0.10 * math.sin(t * 2.3 + _IDLE_PHASE[i] * 2 * math.pi)
                for i in range(n)]
    if state == "error":
        flash = 0.9 if (t % 0.4) < 0.15 else 0.05
        return [flash] * n
    # idle / ready: slow uniform breathing
    breath = 0.10 + 0.06 * math.sin(t * 0.9)
    return [breath] * n


def _draw_aura(draw, cx, cy, t, col, state, eq_levels=None):
    levels = _band_levels(state, t, eq_levels)
    n = len(levels)
    for i in range(n):
        _aura_smoothed[i] = max(levels[i], _aura_smoothed[i] * 0.55)

    mean_level = sum(_aura_smoothed) / n
    core_r = _BASE_R + int(_MOD_R * mean_level)

    if state == "error":
        cx = cx + int(4 * math.sin(t * 30))

    # Soft glow: nested ellipses darkening outward, no alpha layer needed --
    # same cheap trick face_wave.py's pulsing ring already used.
    for k in range(_GLOW_LAYERS, 0, -1):
        rr = core_r + k * 7
        shade = tuple(max(0, c - 38 * k) for c in col)
        draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=shade)
    draw.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], fill=col)

    inner_r = core_r + 6
    w = 5
    for i in range(n):
        ang = -math.pi / 2 + i * (2 * math.pi / n)
        length = inner_r + _MAX_SPOKE * _aura_smoothed[i]
        x0 = cx + inner_r * math.cos(ang)
        y0 = cy + inner_r * math.sin(ang)
        x1 = cx + length * math.cos(ang)
        y1 = cy + length * math.sin(ang)
        draw.line([x0, y0, x1, y1], fill=col, width=w)
        draw.ellipse([x1 - w / 2, y1 - w / 2, x1 + w / 2, y1 + w / 2], fill=col)


# ── Header (state dot + label, clock, battery, aura, separator) ──────────────

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

    _draw_aura(draw, W // 2, _AURA_CX_Y, t, col, state, eq_levels=eq_levels)

    draw.line([0, SEP_Y - 1, W, SEP_Y - 1], fill=(40, 44, 55), width=1)


# ── Public API ────────────────────────────────────────────────────────────────

def draw_frame(state: str, caption: str, t: float, batt=None, mouth_shape=None,
               eq_levels=None) -> Image.Image:
    """
    Render one 240x280 frame. Same signature as face_scroll.draw_frame --
    mouth_shape is accepted and ignored (the aura has no lipsync concept;
    kept so this is a true drop-in for the _face_loop() call site).
    """
    img  = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    col  = _STATE_COLORS.get(state, (120, 120, 120))

    _draw_header(img, draw, state, col, batt=batt, t=t, eq_levels=eq_levels)
    _draw_text_area(img, draw, caption, t)

    return img
