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
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

W, H       = 240, 280
HEADER_H   = 88
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


def _draw_icon(img, draw, state, col):
    """Draw the state emoji centred in the header icon area."""
    cx, cy = W // 2, 52   # centre of icon zone

    char  = _EMOJI.get(state, "?")
    noto  = _get_noto()
    symb  = _get_symb()

    if noto:
        # Noto Color Emoji: render at bitmap size into a temp RGBA image,
        # then scale down and paste onto the main image.
        tmp_w, tmp_h = 140, 140
        tmp = Image.new("RGBA", (tmp_w, tmp_h), (0, 0, 0, 0))
        tmp_draw = ImageDraw.Draw(tmp)
        tmp_draw.text((2, 2), char, font=noto, embedded_color=True)
        # Crop to non-transparent bounding box
        bbox = tmp.getbbox()
        if bbox:
            tmp = tmp.crop(bbox)
        # Scale to target size, keeping aspect ratio
        tw, th = tmp.size
        scale  = _emoji_size / max(tw, th)
        new_w  = max(1, int(tw * scale))
        new_h  = max(1, int(th * scale))
        tmp    = tmp.resize((new_w, new_h), Image.LANCZOS)
        # Paste centred
        px = cx - new_w // 2
        py = cy - new_h // 2
        img.paste(tmp, (px, py), tmp)

    elif symb:
        # Symbola: monochrome vector font, tint with state colour
        bb  = draw.textbbox((0, 0), char, font=symb)
        tw  = bb[2] - bb[0]
        th  = bb[3] - bb[1]
        draw.text((cx - tw // 2 - bb[0], cy - th // 2 - bb[1]),
                  char, font=symb, fill=col)

    else:
        # Last resort: circled letter
        r = _emoji_size // 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=3)
        font_fb = _font(_FONT_BOLD, 28)
        letter  = state[0].upper()
        bb      = draw.textbbox((0, 0), letter, font=font_fb)
        draw.text((cx - (bb[2] - bb[0]) // 2, cy - (bb[3] - bb[1]) // 2),
                  letter, font=font_fb, fill=col)


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


def _draw_header(img, draw, state, col, batt=None):
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
    else:
        draw.text((W - clock_w - 8, 5), clock, font=font_sm, fill=(130, 140, 170))

    # Emoji icon centred in remaining header space
    _draw_icon(img, draw, state, col)

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


# ── Public API ────────────────────────────────────────────────────────────────

def draw_frame(state: str, caption: str, t: float, batt=None) -> Image.Image:
    """
    Render one 240×280 frame.

    Parameters
    ----------
    state   : "idle" | "ready" | "listening" | "thinking" | "speaking" | "error"
    caption : full reply text to show in the scrolling area
    t       : time.time() — drives clock, scroll, and icon animations
    batt    : (level: float|None, charging: bool) from get_battery(), or None
    """
    img  = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    col  = _STATE_COLORS.get(state, (120, 120, 120))

    _draw_header(img, draw, state, col, batt=batt)
    _draw_text_area(img, draw, caption, t)

    return img
