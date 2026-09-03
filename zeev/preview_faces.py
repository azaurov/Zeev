#!/usr/bin/env python3
"""Generate PNG preview frames for all three Zeev face renderers.

Output: zeev/previews/{face,wave,scroll}_{state}.png + grid_{face,wave,scroll}.png
"""

import math
import os
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE     = Path(__file__).parent
OUT_DIR  = HERE / "previews"
OUT_DIR.mkdir(exist_ok=True)

STATES   = ["idle", "ready", "listening", "thinking", "speaking", "error"]
CAPTION  = ("The capital of France is Paris, one of the most visited cities "
            "in the world. It sits on the River Seine in northern France.")
T        = time.time()          # fixed timestamp so all frames are comparable
T_SPEAK  = T + 0.09            # offset so speaking mouth is open


# ── Animated face (extracted from run_device_mode) ───────────────────────────

W, H = 240, 280
_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

_FACE_CX, _FACE_CY, _FACE_R = 120, 112, 88

_STATE_COLORS = {
    "idle":      (50,  120, 220),
    "ready":     (0,   160, 255),
    "listening": (0,   210, 230),
    "thinking":  (210, 155,  10),
    "speaking":  (40,  210,  90),
    "error":     (220,  60,  60),
}


def _load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _wrap_text(draw, text, font, max_w):
    words = text.split()
    lines, line = [], []
    for w in words:
        test = " ".join(line + [w])
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_w and line:
            lines.append(" ".join(line))
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(" ".join(line))
    return lines


def draw_face(state, mouth_open=False, caption="", blink=False, breath=0.0):
    img  = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    col  = _STATE_COLORS.get(state, (120, 120, 120))

    cx, cy = _FACE_CX, _FACE_CY
    if state in ("idle", "ready"):
        r = _FACE_R + int(math.sin(breath) * 3)
    else:
        r = _FACE_R

    glow_col = tuple(max(0, c - 140) for c in col)
    draw.ellipse([cx-r-6, cy-r-6, cx+r+6, cy+r+6], fill=glow_col)
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(22, 28, 45), outline=col, width=4)

    for ex, ey in [(cx-30, cy-22), (cx+30, cy-22)]:
        if blink:
            draw.line([ex-12, ey, ex+12, ey], fill=(235, 235, 255), width=3)
        else:
            draw.ellipse([ex-13, ey-13, ex+13, ey+13], fill=(235, 235, 255))
            if state == "thinking":
                draw.ellipse([ex+1, ey-8, ex+9, ey+1], fill=(18, 18, 30))
            elif state == "listening":
                draw.ellipse([ex-7, ey-7, ex+7, ey+7], fill=(18, 18, 30))
            else:
                draw.ellipse([ex-5, ey-5, ex+5, ey+5], fill=(18, 18, 30))

            if not blink:
                brow_y = ey - 18
                if state == "listening":
                    draw.line([ex-13, brow_y+4, ex+13, brow_y-2], fill=col, width=3)
                elif state == "thinking":
                    draw.line([ex-13, brow_y-2, ex+13, brow_y+4], fill=col, width=3)
                else:
                    draw.line([ex-13, brow_y, ex+13, brow_y], fill=col, width=2)

    mouth_y = cy + 28
    if state == "listening":
        draw.ellipse([cx-8, mouth_y-6, cx+8, mouth_y+10], outline=(210, 150, 160), width=2)
    elif state == "error":
        draw.arc([cx-26, mouth_y+4, cx+26, mouth_y+28], start=195, end=345, fill=(210, 80, 80), width=3)
    elif state == "speaking" and mouth_open:
        draw.ellipse([cx-22, mouth_y-5, cx+22, mouth_y+20], fill=(160, 50, 60))
        draw.ellipse([cx-22, mouth_y-5, cx+22, mouth_y+5],  fill=(210, 110, 120))
    else:
        draw.arc([cx-26, mouth_y-8, cx+26, mouth_y+22], start=15, end=165, fill=(210, 150, 160), width=3)

    if caption:
        font_sm = _load_font(_FONT_PATH, 14)
        lines   = _wrap_text(draw, caption, font_sm, W - 20)
        y       = cy + r + 10
        for ln in lines[:3]:
            bbox = draw.textbbox((0, 0), ln, font=font_sm)
            tw   = bbox[2] - bbox[0]
            draw.text(((W - tw) // 2, y), ln, font=font_sm, fill=(200, 200, 200))
            y += 18
            if y > H - 18:
                break

    font_lbl = _load_font(_FONT_PATH, 12)
    labels   = {"idle": "Press to start", "ready": "Your turn",
                "listening": "Listening...", "thinking": "Thinking...",
                "speaking": "Speaking...", "error": "Error"}
    label    = labels.get(state, state)
    bbox     = draw.textbbox((0, 0), label, font=font_lbl)
    tw       = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, H - 16), label, font=font_lbl, fill=col)
    return img


# ── Grid builder ──────────────────────────────────────────────────────────────

def make_grid(frames, labels, title):
    """Build a labeled grid: one column per state, with a title bar."""
    n      = len(frames)
    pad    = 6
    lbl_h  = 20
    title_h = 28
    cell_w = W + pad
    cell_h = H + lbl_h + pad
    grid_w = cell_w * n + pad
    grid_h = title_h + cell_h + pad

    grid  = Image.new("RGB", (grid_w, grid_h), (15, 15, 20))
    draw  = ImageDraw.Draw(grid)
    font  = _load_font(_FONT_BOLD, 14)
    tfont = _load_font(_FONT_BOLD, 16)

    # Title
    tb = draw.textbbox((0, 0), title, font=tfont)
    draw.text(((grid_w - (tb[2]-tb[0])) // 2, (title_h - (tb[3]-tb[1])) // 2),
              title, font=tfont, fill=(200, 210, 230))

    for i, (frame, label) in enumerate(zip(frames, labels)):
        x = pad + i * cell_w
        y = title_h + pad
        grid.paste(frame, (x, y))
        lb = draw.textbbox((0, 0), label, font=font)
        draw.text((x + (W - (lb[2]-lb[0])) // 2, y + H + 3),
                  label, font=font, fill=(140, 150, 170))

    return grid


# ── Generate previews ─────────────────────────────────────────────────────────

sys.path.insert(0, str(HERE))
import face_wave
import face_scroll
import face_aura
import face_eyes

# Synthetic eq_levels for the "speaking" aura/scroll preview frame -- same
# 8-value shape _draw_equalizer/_draw_aura expect from a live daemon poll,
# hand-picked here so the preview isn't just the flat synthetic fallback.
_PREVIEW_EQ = [0.8, 0.4, 0.9, 0.3, 0.6, 0.95, 0.5, 0.7]

renderers = {
    "face":   lambda s: draw_face(s,
                  mouth_open=(s == "speaking"),
                  caption=CAPTION,
                  breath=T * 1.2),
    "wave":   lambda s: face_wave.draw_frame(s, CAPTION, T),
    "scroll": lambda s: face_scroll.draw_frame(s, CAPTION, T,
                  eq_levels=_PREVIEW_EQ if s == "speaking" else None),
    "aura":   lambda s: face_aura.draw_frame(s, CAPTION, T,
                  eq_levels=_PREVIEW_EQ if s == "speaking" else None),
    "eyes":   lambda s: face_eyes.draw_frame(s, CAPTION, T,
                  eq_levels=_PREVIEW_EQ if s == "speaking" else None),
}

for name, fn in renderers.items():
    frames = []
    for state in STATES:
        img = fn(state)
        img.save(OUT_DIR / f"{name}_{state}.png")
        frames.append(img)
        print(f"  {name}/{state}")
    grid = make_grid(frames, STATES, f"Zeev — {name} style")
    grid.save(OUT_DIR / f"grid_{name}.png")
    print(f"  → grid_{name}.png  ({grid.width}×{grid.height})")

print(f"\nAll previews saved to {OUT_DIR}/")
