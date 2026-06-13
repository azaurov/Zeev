"""
Waveform display face for Zeev device mode (240×280 ST7789).

Drop-in alternative to the animated-face renderer in run_device_mode().

Swap in by replacing the two lines in _face_loop() that build and push
the image:

    # was:
    img = _draw_face_img(state, _mouth_open, caption, blink=_blink, breath=breath)
    # becomes:
    from face_wave import draw_frame
    img = draw_frame(state, caption, time.time())

The _blink / _mouth_open / _breath variables are not needed; the loop
interval should be set to 0.08 s unconditionally (smooth waveform needs
~12 fps).

Visual per state
────────────────
  idle / ready   : live clock (HH:MM) inside a pulsing ring
  listening      : gentle low-amplitude waveform bars (mic waiting)
  speaking       : energetic waveform bars (voice output)
  thinking       : five bouncing dots (loading indicator)
  error          : circled X
"""

import math
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

W, H = 240, 280

_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

_STATE_COLORS = {
    "idle":      (50,  120, 220),
    "ready":     (0,   160, 255),
    "listening": (0,   210, 230),
    "thinking":  (210, 155,  10),
    "speaking":  (40,  210,  90),
    "error":     (220,  60,  60),
}

_LABELS = {
    "idle":      "Press to start",
    "ready":     "Your turn",
    "listening": "Listening",
    "thinking":  "Thinking",
    "speaking":  "Speaking",
    "error":     "Error",
}

# Waveform geometry
_BARS      = 22
_BAR_W     = 6
_BAR_GAP   = 4
_BAR_PITCH = _BAR_W + _BAR_GAP                          # 10 px
_TOTAL_W   = _BARS * _BAR_PITCH - _BAR_GAP              # 214 px
_BAR_X0    = (W - _TOTAL_W) // 2                        # ~13 px
_WAVE_CY   = 138                                        # vertical centre of waveform
_WAVE_MAXH = 68                                         # max half-height (speaking)
_WAVE_MINH = 3                                          # min half-height
_LISTEN_MAXH = 28                                       # max half-height (listening)


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    words = text.split()
    lines, line = [], []
    for w in words:
        test = " ".join(line + [w])
        if draw.textbbox((0, 0), test, font=font)[2] > max_w and line:
            lines.append(" ".join(line))
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(" ".join(line))
    return lines


def _bar_heights(t, maxh, minh, freqs, phases, weights):
    """Return list of half-heights for each bar using summed sine waves."""
    heights = []
    for i in range(_BARS):
        v = sum(w * math.sin(t * f + i * p) for f, p, w in zip(freqs, phases, weights))
        # v ∈ (-1, 1) roughly; map to [minh, maxh]
        norm = (v + 1.0) / 2.0          # 0..1
        heights.append(int(minh + (maxh - minh) * norm))
    return heights


def _draw_waveform(draw, col, heights):
    """Draw rounded waveform bars centred at _WAVE_CY."""
    n = len(heights)
    for i, hh in enumerate(heights):
        x0 = _BAR_X0 + i * _BAR_PITCH
        x1 = x0 + _BAR_W
        y0 = _WAVE_CY - hh
        y1 = _WAVE_CY + hh
        # Edge bars slightly dimmer for a natural envelope
        brightness = 1.0 - 0.45 * (abs(i - n // 2) / (n // 2))
        bc = tuple(max(0, min(255, int(c * brightness))) for c in col)
        # Rounded rect: draw as ellipse top/bottom + rect body
        r = _BAR_W // 2
        if y1 - y0 >= _BAR_W:
            draw.rectangle([x0, y0 + r, x1, y1 - r], fill=bc)
            draw.ellipse([x0, y0, x1, y0 + _BAR_W], fill=bc)
            draw.ellipse([x0, y1 - _BAR_W, x1, y1], fill=bc)
        else:
            draw.ellipse([x0, y0, x1, y1], fill=bc)


def _draw_clock(draw, col, t, pulse_r):
    """Large clock with a pulsing ring for idle/ready states."""
    cx, cy = W // 2, 128
    # Outer glow ring
    glow = tuple(max(0, c - 150) for c in col)
    draw.ellipse([cx - pulse_r - 4, cy - pulse_r - 4,
                  cx + pulse_r + 4, cy + pulse_r + 4], fill=glow)
    # Ring
    draw.ellipse([cx - pulse_r, cy - pulse_r,
                  cx + pulse_r, cy + pulse_r], outline=col, width=3)

    now = datetime.now()
    hhmm = now.strftime("%H:%M")
    secs = now.strftime("%S")
    date = now.strftime("%a %d %b")

    font_big  = _font(_FONT_BOLD, 46)
    font_sec  = _font(_FONT_PATH, 18)
    font_date = _font(_FONT_PATH, 15)

    # HH:MM
    bb = draw.textbbox((0, 0), hhmm, font=font_big)
    tw = bb[2] - bb[0]
    draw.text(((W - tw) // 2, cy - (bb[3] - bb[1]) // 2 - 4), hhmm,
              font=font_big, fill=(230, 235, 255))

    # :SS smaller, right-aligned to clock
    bb2 = draw.textbbox((0, 0), ":" + secs, font=font_sec)
    draw.text(((W + tw) // 2 + 2, cy - (bb2[3] - bb2[1]) // 2 + 6),
              ":" + secs, font=font_sec, fill=col)

    # Date below
    bb3 = draw.textbbox((0, 0), date, font=font_date)
    draw.text(((W - (bb3[2] - bb3[0])) // 2, cy + 34), date,
              font=font_date, fill=(130, 140, 170))


def _draw_thinking(draw, col, t):
    """Five bouncing dots in a row."""
    cx, cy = W // 2, _WAVE_CY
    n   = 5
    sep = 30
    xs  = [cx + (i - n // 2) * sep for i in range(n)]
    for i, x in enumerate(xs):
        dy = int(18 * math.sin(t * 4.5 + i * 0.65))
        r  = 9
        brightness = 0.6 + 0.4 * math.sin(t * 4.5 + i * 0.65 + math.pi / 2)
        bc = tuple(max(0, min(255, int(c * brightness))) for c in col)
        draw.ellipse([x - r, cy - dy - r, x + r, cy - dy + r], fill=bc)


def _draw_error(draw, col):
    """Circled X."""
    cx, cy, r = W // 2, _WAVE_CY, 44
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=4)
    off = 22
    draw.line([cx - off, cy - off, cx + off, cy + off], fill=col, width=5)
    draw.line([cx + off, cy - off, cx - off, cy + off], fill=col, width=5)


def _draw_caption(draw, caption, bottom_y):
    """Up to 3 wrapped lines above bottom_y."""
    if not caption:
        return
    font = _font(_FONT_PATH, 14)
    lines = _wrap(draw, caption, font, W - 24)
    line_h = 18
    y = bottom_y - len(lines[:3]) * line_h
    for ln in lines[:3]:
        bb = draw.textbbox((0, 0), ln, font=font)
        draw.text(((W - (bb[2] - bb[0])) // 2, y), ln,
                  font=font, fill=(195, 200, 215))
        y += line_h


def _draw_state_label(draw, state, col):
    """State text top-left with a small coloured dot."""
    label = _LABELS.get(state, state)
    font  = _font(_FONT_PATH, 13)
    dot_r = 5
    draw.ellipse([10, 10, 10 + dot_r * 2, 10 + dot_r * 2], fill=col)
    draw.text((24, 8), label, font=font, fill=col)


# ── Public API ───────────────────────────────────────────────────────────────

def draw_frame(state: str, caption: str, t: float) -> Image.Image:
    """
    Render one frame for the 240×280 LCD.

    Parameters
    ----------
    state   : "idle" | "ready" | "listening" | "thinking" | "speaking" | "error"
    caption : short text to show at the bottom (last user query or reply snippet)
    t       : current time (time.time()) — drives all animations
    """
    img  = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    col  = _STATE_COLORS.get(state, (120, 120, 120))

    if state in ("idle", "ready"):
        # Pulsing ring radius: 72 ±6 px
        pulse_r = 72 + int(6 * math.sin(t * 1.2))
        _draw_clock(draw, col, t, pulse_r)

    elif state == "listening":
        # Gentle low-energy waveform — waiting for voice
        heights = _bar_heights(
            t,
            maxh=_LISTEN_MAXH, minh=_WAVE_MINH,
            freqs=[1.8, 2.9, 1.2],
            phases=[0.9, 1.5, 0.4],
            weights=[0.55, 0.30, 0.15],
        )
        _draw_waveform(draw, col, heights)

    elif state == "speaking":
        # Energetic multi-layer waveform
        heights = _bar_heights(
            t,
            maxh=_WAVE_MAXH, minh=_WAVE_MINH,
            freqs=[6.1, 3.7, 9.3, 2.1],
            phases=[0.62, 1.10, 0.30, 1.70],
            weights=[0.45, 0.28, 0.17, 0.10],
        )
        _draw_waveform(draw, col, heights)

    elif state == "thinking":
        _draw_thinking(draw, col, t)

    elif state == "error":
        _draw_error(draw, col)

    _draw_state_label(draw, state, col)
    _draw_caption(draw, caption, H - 18)

    return img
