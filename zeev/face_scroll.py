"""
Status-header + scrolling-text face for Zeev device mode (240×280 ST7789).

Inspired by the PiSugar official whisplay-ai-chatbot layout:
  - Header (88 px): large state emoji + label + clock
  - Main area (192 px): full reply text, word-wrapped, auto-scrolls on overflow

Emoji rendering priority:
  1. Noto Color Emoji (color, only available at bitmap size 109 → scaled to fit)
  2. Symbola (monochrome vector, tinted with state colour)
  3. PIL-drawn geometric icon (no font needed)

Drop-in alternative to the animated face in run_device_mode():

    # replace:
    img = _draw_face_img(state, _mouth_open, caption, blink=_blink, breath=breath)
    # with:
    from face_scroll import draw_frame
    img = draw_frame(state, full_reply_text, time.time())

Pass the *full* reply string as `caption` (not just a short snippet) for best
results — the text area auto-scrolls so longer replies are fully readable.

Loop interval: set unconditionally to 0.08 s (12 fps) — needed for smooth
scroll and icon animations.
"""

import math
import os
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

W, H       = 240, 280
HEADER_H   = 132
SEP_Y      = HEADER_H          # separator line y-position
TEXT_Y     = HEADER_H + 1      # text area starts 1 px below separator
TEXT_H     = H - TEXT_Y        # 191 px

_FONT_PATH  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_BOLD  = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_NOTO  = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
_FONT_SYMB  = "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf"
_NOTO_SIZE  = 109   # only valid bitmap strike size for NotoColorEmoji

_STATE_COLORS = {
    "idle":      (50,  120, 220),
    "ready":     (0,   160, 255),
    "listening": (0,   210, 230),
    "thinking":  (210, 155,  10),
    "speaking":  (40,  210,  90),
    "error":     (220,  60,  60),
}

_EMOJI = {
    "idle":      "😴",
    "ready":     "😊",
    "listening": "🎤",
    "thinking":  "🤔",
    "speaking":  "🗣",
    "error":     "❌",
}

_LABELS = {
    "idle":      "Idle",
    "ready":     "Ready",
    "listening": "Listening",
    "thinking":  "Thinking",
    "speaking":  "Speaking",
    "error":     "Error",
}

# Text scroll parameters
_HOLD_TOP_S    = 1.5   # seconds to hold before scrolling starts
_HOLD_BOTTOM_S = 2.0   # seconds to hold at bottom before looping
_SCROLL_SPEED  = 28    # px / second

# Module-level scroll state — reset whenever the text changes
_scroll = {
    "text":       "",
    "top":        0.0,   # current scroll offset in px
    "max_top":    0,     # total scrollable distance
    "updated_at": 0.0,   # time.time() when text last changed
    "at_bottom":  False,
    "bottom_at":  0.0,   # time.time() when we reached bottom
}


# ── Font helpers ─────────────────────────────────────────────────────────────

def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


# ── Text wrapping ─────────────────────────────────────────────────────────────

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
    return lines or [""]


# ── Emoji icon rendering ──────────────────────────────────────────────────────

# Cached font objects (loaded once)
_noto_font   = None
_symb_font   = None
_emoji_size  = 52   # target display size in px

def _get_noto():
    global _noto_font
    if _noto_font is None:
        try:
            _noto_font = ImageFont.truetype(_FONT_NOTO, _NOTO_SIZE)
        except Exception:
            _noto_font = False
    return _noto_font if _noto_font else None

def _get_symb():
    global _symb_font
    if _symb_font is None:
        try:
            _symb_font = ImageFont.truetype(_FONT_SYMB, _emoji_size)
        except Exception:
            _symb_font = False
    return _symb_font if _symb_font else None


_MOUTH_SHAPES = {
    # Rendered relative to (cx, my, s) where s is the face scale factor;
    # ink is the closed-mouth line color.
    "closed": lambda draw, cx, my, ink, s: draw.line(
        [cx - 6 * s, my, cx + 6 * s, my], fill=ink, width=max(2, round(2 * s))),
    "half":   lambda draw, cx, my, ink, s: draw.ellipse(
        [cx - 5 * s, my - 2 * s, cx + 5 * s, my + 3 * s], fill=(155, 52, 52)),
    "open":   lambda draw, cx, my, ink, s: draw.ellipse(
        [cx - 6 * s, my - 4 * s, cx + 6 * s, my + 6 * s], fill=(155, 52, 52)),
    "e":      lambda draw, cx, my, ink, s: draw.ellipse(
        [cx - 8 * s, my - 2 * s, cx + 8 * s, my + 2 * s], fill=(155, 52, 52)),
    "u":      lambda draw, cx, my, ink, s: draw.ellipse(
        [cx - 3 * s, my - 3 * s, cx + 3 * s, my + 3 * s], fill=(155, 52, 52)),
}

_FACE_R = 40  # face radius — was 25; grown to make the mouth/lipsync legible
_FACE_R_ORIG = 25  # radius the original hand-tuned pixel offsets were designed for
_HAT_TOP = 20  # crown top y — fixed, clears the status-row text above it


def _draw_miss_minutes_icon(img, draw, state, t, mouth_shape=None):
    """Draw Miss Minutes — animated PIL clock face with cartoon expressions.

    mouth_shape: "closed"|"half"|"open"|"e"|"u" from the audio-driven lipsync
    engine (see lipsync.py), used during "speaking" instead of the fixed-rate
    flap. None falls back to the old timer-based open/closed toggle.
    """
    r = _FACE_R
    s = r / _FACE_R_ORIG   # scale factor for offsets hand-tuned at r=25
    # Crown height scales with s so the brim (which extends 5*s above
    # face_top) never grows back up into the fixed status-row text.
    face_top = _HAT_TOP + 15 * s
    cx, cy = W // 2, face_top + r

    # Blink: eyes closed for 0.15 s every 4 s
    blink = (t % 4.0) < 0.15

    # ── Cowboy hat ───────────────────────────────────────────────────────────
    hat = (48, 32, 10)
    # Crown: narrow rect above face, top at y≈20 (clears status-row text)
    draw.rounded_rectangle([cx - 13 * s, 20, cx + 13 * s, face_top + 2 * s],
                            radius=4 * s, fill=hat)
    # Brim: wide ellipse overlapping the crown–face join
    draw.ellipse([cx - 22 * s, face_top - 5 * s, cx + 22 * s, face_top + 6 * s], fill=hat)

    # ── Clock face ───────────────────────────────────────────────────────────
    face_col = (228, 192, 40)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                 fill=face_col, outline=(148, 112, 5), width=2)

    # Hour tick marks (major at 3/6/9/12, minor elsewhere)
    for i in range(12):
        a   = math.pi * 2 * i / 12 - math.pi / 2
        r1  = r - 2 * s
        r2  = r - (7 if i % 3 == 0 else 5) * s
        w   = 2 if i % 3 == 0 else 1
        draw.line([cx + int(r1 * math.cos(a)), cy + int(r1 * math.sin(a)),
                   cx + int(r2 * math.cos(a)), cy + int(r2 * math.sin(a))],
                  fill=(100, 72, 4), width=w)

    # Clock hands — real current time
    now = datetime.now()
    ha = math.pi * 2 * (now.hour % 12 + now.minute / 60) / 12 - math.pi / 2
    ma = math.pi * 2 * now.minute / 60 - math.pi / 2
    hr_len = int(r * 0.48)
    mn_len = int(r * 0.68)
    draw.line([cx, cy,
               cx + int(hr_len * math.cos(ha)), cy + int(hr_len * math.sin(ha))],
              fill=(55, 36, 4), width=3)
    draw.line([cx, cy,
               cx + int(mn_len * math.cos(ma)), cy + int(mn_len * math.sin(ma))],
              fill=(55, 36, 4), width=2)
    # Centre pin
    draw.ellipse([cx - 2 * s, cy - 2 * s, cx + 2 * s, cy + 2 * s], fill=(38, 22, 4))

    # ── Eyes ─────────────────────────────────────────────────────────────────
    eye_y = cy - 4 * s
    ink   = (22, 14, 3)
    for ex in (cx - 8 * s, cx + 8 * s):
        if blink or state == "idle":
            # Heavy-lidded / closed: flat line with lower arc
            draw.line([ex - 4 * s, eye_y, ex + 4 * s, eye_y], fill=ink, width=max(2, round(2 * s)))
            if not blink:
                draw.arc([ex - 4 * s, eye_y - 1 * s, ex + 4 * s, eye_y + 5 * s],
                         start=0, end=180, fill=ink, width=max(2, round(2 * s)))
        elif state == "error":
            draw.line([ex - 3 * s, eye_y - 3 * s, ex + 3 * s, eye_y + 3 * s], fill=ink, width=max(2, round(2 * s)))
            draw.line([ex + 3 * s, eye_y - 3 * s, ex - 3 * s, eye_y + 3 * s], fill=ink, width=max(2, round(2 * s)))
        elif state == "thinking":
            # Squinting upward
            draw.arc([ex - 4 * s, eye_y - 4 * s, ex + 4 * s, eye_y + 2 * s],
                     start=200, end=340, fill=ink, width=max(2, round(2 * s)))
        else:
            # Open dot
            draw.ellipse([ex - 3 * s, eye_y - 3 * s, ex + 3 * s, eye_y + 3 * s], fill=ink)

    # ── Mouth ────────────────────────────────────────────────────────────────
    my = cy + 13 * s
    if state == "speaking" and mouth_shape is not None:
        _MOUTH_SHAPES.get(mouth_shape, _MOUTH_SHAPES["closed"])(draw, cx, my, ink, s)
    elif state == "speaking" and (t % 0.5) < 0.28:
        # Fallback flap when no lipsync data is available yet this utterance.
        draw.ellipse([cx - 6 * s, my - 4 * s, cx + 6 * s, my + 6 * s], fill=(155, 52, 52))
    elif state == "error":
        draw.arc([cx - 7 * s, my - 4 * s, cx + 7 * s, my + 4 * s], start=200, end=340, fill=ink, width=max(2, round(2 * s)))
    elif state == "ready":
        draw.arc([cx - 10 * s, my - 8 * s, cx + 10 * s, my + 6 * s], start=10, end=170, fill=ink, width=max(2, round(2 * s)))
    else:
        draw.arc([cx - 8 * s, my - 6 * s, cx + 8 * s, my + 4 * s], start=15, end=165, fill=ink, width=max(2, round(2 * s)))


# ── Header ────────────────────────────────────────────────────────────────────

def _draw_battery(draw, x, y, level, charging):
    """Draw a small battery icon at (x, y) — level 0–100, charging bool."""
    bw, bh = 22, 11
    nub_w   = 3

    if charging:
        col = (80, 220, 120)
    elif level is not None and level < 20:
        col = (220, 60, 60)
    elif level is not None and level < 50:
        col = (220, 180, 40)
    else:
        col = (100, 130, 170)

    # Outline
    draw.rectangle([x, y, x + bw - 1, y + bh - 1], outline=col, width=1)
    # Nub
    draw.rectangle([x + bw, y + 3, x + bw + nub_w - 1, y + bh - 4], fill=col)
    # Fill bar
    if level is not None:
        fill_w = max(1, int((bw - 4) * level / 100))
        draw.rectangle([x + 2, y + 2, x + 2 + fill_w - 1, y + bh - 3], fill=col)
    # Charging bolt
    if charging:
        cx = x + bw // 2
        draw.line([cx + 2, y + 2, cx - 1, y + bh // 2], fill=(0, 0, 0), width=1)
        draw.line([cx - 1, y + bh // 2, cx + 2, y + bh - 3], fill=(0, 0, 0), width=1)


def _draw_header(img, draw, state, col, batt=None, t=0.0, mouth_shape=None):
    """batt: (level_float_or_None, charging_bool) or None if unavailable."""
    label = _LABELS.get(state, state)
    clock = datetime.now().strftime("%H:%M")

    font_sm  = _font(_FONT_PATH, 13)
    font_lbl = _font(_FONT_BOLD, 14)

    # Top row: coloured dot + bold label left, clock + battery right
    dot_r = 5
    draw.ellipse([8, 6, 8 + dot_r * 2, 6 + dot_r * 2], fill=col)
    draw.text((22, 4), label, font=font_lbl, fill=col)

    bb = draw.textbbox((0, 0), clock, font=font_sm)
    clock_w = bb[2] - bb[0]

    if batt is not None:
        level, charging = batt
        batt_x = W - 22 - 3 - 3 - 8   # battery right-aligned with margin
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

    # Miss Minutes clock-face icon
    _draw_miss_minutes_icon(img, draw, state, t, mouth_shape=mouth_shape)

    # Separator line
    draw.line([0, SEP_Y - 1, W, SEP_Y - 1], fill=(40, 44, 55), width=1)


# ── Scrolling text area ───────────────────────────────────────────────────────

def _update_scroll(text, t, line_h, n_lines):
    """Advance the scroll offset; reset if text changed."""
    global _scroll
    max_top = max(0, n_lines * line_h - TEXT_H)

    if text != _scroll["text"]:
        _scroll["text"]       = text
        _scroll["top"]        = 0.0
        _scroll["max_top"]    = max_top
        _scroll["updated_at"] = t
        _scroll["at_bottom"]  = False
        _scroll["bottom_at"]  = 0.0
        return 0.0

    _scroll["max_top"] = max_top

    if max_top <= 0:
        return 0.0

    elapsed = t - _scroll["updated_at"]

    if elapsed < _HOLD_TOP_S:
        return 0.0

    if _scroll["at_bottom"]:
        if t - _scroll["bottom_at"] >= _HOLD_BOTTOM_S:
            # Loop back to top
            _scroll["top"]        = 0.0
            _scroll["at_bottom"]  = False
            _scroll["updated_at"] = t
        return _scroll["top"]

    scroll_elapsed = elapsed - _HOLD_TOP_S
    new_top = min(max_top, scroll_elapsed * _SCROLL_SPEED)
    _scroll["top"] = new_top

    if new_top >= max_top and not _scroll["at_bottom"]:
        _scroll["at_bottom"] = True
        _scroll["bottom_at"] = t

    return new_top


def _draw_text_area(img, draw, text, t):
    if not text:
        hint_font = _font(_FONT_PATH, 14)
        hint = "Press button to speak"
        bb = draw.textbbox((0, 0), hint, font=hint_font)
        x = (W - (bb[2] - bb[0])) // 2
        y = TEXT_Y + (TEXT_H - (bb[3] - bb[1])) // 2
        draw.text((x, y), hint, font=hint_font, fill=(60, 65, 80))
        return

    font   = _font(_FONT_PATH, 18)
    line_h = 24
    pad_x  = 10

    lines   = _wrap(draw, text, font, W - pad_x * 2)
    scroll  = _update_scroll(text, t, line_h, len(lines))
    offset  = int(scroll)

    y = TEXT_Y + 6 - offset
    for ln in lines:
        if y + line_h < TEXT_Y:
            y += line_h
            continue
        if y > H:
            break
        # Clip partial lines at top and bottom edges with alpha fade
        if y < TEXT_Y or y + line_h > H:
            alpha = min(
                max(0.0, (y + line_h - TEXT_Y) / line_h),
                max(0.0, (H - y) / line_h),
            )
            v = int(200 * alpha)
            c = (v, v, v)
        else:
            c = (200, 205, 220)
        draw.text((pad_x, y), ln, font=font, fill=c)
        y += line_h

    # Scroll indicator: thin bar on right edge
    max_top = _scroll["max_top"]
    if max_top > 0:
        track_h = TEXT_H - 16
        track_x = W - 4
        draw.line([track_x, TEXT_Y + 8, track_x, TEXT_Y + 8 + track_h],
                  fill=(40, 44, 55), width=2)
        thumb_h   = max(16, int(track_h * TEXT_H / (TEXT_H + max_top)))
        thumb_top = int((track_h - thumb_h) * min(1.0, offset / max_top)) if max_top else 0
        draw.rounded_rectangle(
            [track_x - 1, TEXT_Y + 8 + thumb_top,
             track_x + 1, TEXT_Y + 8 + thumb_top + thumb_h],
            radius=1, fill=(100, 110, 140),
        )


# ── Miss Minutes idle animation ───────────────────────────────────────────────

_MM_WEBP = os.path.join(os.path.dirname(__file__), "data", "miss_minutes_idle.webp")
_mm_frames: list = []
_mm_loaded = False

def _load_mm_frames():
    global _mm_frames, _mm_loaded
    if _mm_loaded:
        return bool(_mm_frames)
    _mm_loaded = True
    if not os.path.exists(_MM_WEBP):
        return False
    try:
        src = Image.open(_MM_WEBP)
        n = getattr(src, "n_frames", 1)
        for i in range(n):
            src.seek(i)
            frame = src.convert("RGBA")
            sw, sh = frame.size
            # Scale to fill full height (280px), then center-crop to 240px wide
            scale = H / sh
            nw, nh = int(sw * scale), H
            frame = frame.resize((nw, nh), Image.LANCZOS)
            bg = Image.new("RGB", (W, H), (0, 0, 0))
            x = (nw - W) // 2
            bg.paste(frame, (-x, 0), frame)
            _mm_frames.append(bg)
        return bool(_mm_frames)
    except Exception as e:
        print(f"MM idle load error: {e}")
        return False


def _draw_idle_mm(t: float) -> Image.Image | None:
    """Return a full-screen Miss Minutes animation frame, or None if unavailable."""
    if not _load_mm_frames():
        return None
    # Matched to the idle push rate (~6fps, zeev.py _FACE_INTERVAL["idle"] = 1/6)
    # so each push advances ~1 frame instead of skipping unevenly (was *10 — a
    # full loop every 1s, faster than the push rate could render smoothly).
    idx = int(t * 6) % len(_mm_frames)
    return _mm_frames[idx].copy()


# ── Public API ────────────────────────────────────────────────────────────────

def draw_frame(state: str, caption: str, t: float, batt=None, mouth_shape=None) -> Image.Image:
    """
    Render one 240×280 frame.

    Parameters
    ----------
    state       : "idle" | "ready" | "listening" | "thinking" | "speaking" | "error"
    caption     : full reply text to show in the scrolling area
    t           : time.time() — drives clock, scroll, and icon animations
    batt        : (level: float|None, charging: bool) from get_battery(), or None
    mouth_shape : "closed"|"half"|"open"|"e"|"u" from the lipsync engine, or None
                  to fall back to the fixed-rate flap during "speaking".
    """
    if state == "idle":
        img = _draw_idle_mm(t)
        if img:
            return img

    img  = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    col  = _STATE_COLORS.get(state, (120, 120, 120))

    _draw_header(img, draw, state, col, batt=batt, t=t, mouth_shape=mouth_shape)
    _draw_text_area(img, draw, caption, t)

    return img
