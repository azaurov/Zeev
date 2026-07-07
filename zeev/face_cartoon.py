"""
Cartoon rabbit face for Zeev device mode (240x280 ST7789).

An original bunny-style character (big ears, buck teeth, wisecracking
energy in the spirit of classic Looney-Tunes cartoons — not a
reproduction of any trademarked character design) that reacts to the
live conversation state instead of looping a fixed demo animation.

Drop-in alternative to the animated face in run_device_mode():

    # was:
    from face_scroll import draw_frame
    img = draw_frame(state, caption, now, batt=get_battery())
    # becomes:
    from face_cartoon import draw_frame
    img = draw_frame(state, caption, now, batt=get_battery())

Per-state behavior
-------------------
  idle       : dozing on the ground, ears drooped, eyes closed, slow breathing
  ready      : ears up, alert bounce, big buck-tooth grin, awaiting the button
  listening  : leans in, one ear cocked, wide eyes, mouth in a small "o"
  thinking   : head tilts, eyes squint upward, ears sway slowly
  speaking   : energetic bounce, mouth/teeth animate on a talking cadence
  error      : reddish tint, X eyes, ears drooped, short side-to-side shake
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
    "idle":      "Idle",
    "ready":     "Ready",
    "listening": "Listening",
    "thinking":  "Thinking",
    "speaking":  "Speaking",
    "error":     "Error",
}

_SKY_TOP    = (120, 190, 235)
_SKY_BOTTOM = (200, 230, 245)
_GROUND_Y   = 180
_STAGE_TOP  = 26          # below the header row
_CAPTION_Y0 = _STAGE_TOP + _GROUND_Y   # final-image coords, just below the stage


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
    return lines or [""]


_sky_cache = None


def _sky_gradient(tint=None):
    top, bottom = _SKY_TOP, _SKY_BOTTOM
    if tint is not None:
        top = tuple(int(top[i] * 0.6 + tint[i] * 0.4) for i in range(3))
        bottom = tuple(int(bottom[i] * 0.6 + tint[i] * 0.4) for i in range(3))
    img = Image.new("RGB", (W, _GROUND_Y), bottom)
    px = img.load()
    for y in range(_GROUND_Y):
        f = y / _GROUND_Y
        col = tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3))
        for x in range(W):
            px[x, y] = col
    return img


def _draw_battery(draw, x, y, level, charging):
    bw, bh, nub_w = 22, 11, 3
    if charging:
        col = (80, 220, 120)
    elif level is not None and level < 20:
        col = (220, 60, 60)
    elif level is not None and level < 50:
        col = (220, 180, 40)
    else:
        col = (100, 130, 170)
    draw.rectangle([x, y, x + bw - 1, y + bh - 1], outline=col, width=1)
    draw.rectangle([x + bw, y + 3, x + bw + nub_w - 1, y + bh - 4], fill=col)
    if level is not None:
        fill_w = max(1, int((bw - 4) * level / 100))
        draw.rectangle([x + 2, y + 2, x + 2 + fill_w - 1, y + bh - 3], fill=col)


def _draw_header(draw, state, col, batt):
    label = _LABELS.get(state, state)
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
    else:
        draw.text((W - clock_w - 8, 5), clock, font=font_sm, fill=(130, 140, 170))

    draw.line([0, _STAGE_TOP - 2, W, _STAGE_TOP - 2], fill=(40, 44, 55), width=1)


def _draw_caption(draw, caption):
    if not caption:
        return
    font = _font(_FONT_PATH, 13)
    lines = _wrap(draw, caption, font, W - 16)
    # Backing bar for legibility over the ground
    bar_h = min(len(lines), 4) * 17 + 6
    draw.rectangle([0, _CAPTION_Y0, W, _CAPTION_Y0 + bar_h], fill=(20, 24, 30))
    y = _CAPTION_Y0 + 4
    for ln in lines[:4]:
        bb = draw.textbbox((0, 0), ln, font=font)
        draw.text(((W - (bb[2] - bb[0])) // 2, y), ln, font=font, fill=(210, 215, 225))
        y += 17


# ── Bunny character ─────────────────────────────────────────────────────────

def _draw_ear(stage, base_x, base_y, tilt_deg, length, droop, col_outer, col_inner):
    """One ear: a tapered capsule built upright, then rotated and pasted at
    its base point. Rotate+paste avoids fragile manual polygon math."""
    droop_deg = droop * 65
    angle = tilt_deg + droop_deg  # 0 = straight up

    w_base, w_tip = 15, 9
    h = int(length)
    pad = w_base  # headroom so rotation doesn't clip corners
    ear = Image.new("RGBA", (w_base + pad * 2, h + pad * 2), (0, 0, 0, 0))
    ed = ImageDraw.Draw(ear)
    cx0 = ear.size[0] // 2
    # Outer tapered capsule (wide at base/bottom, narrow at tip/top)
    ed.polygon([
        (cx0 - w_base / 2, pad + h), (cx0 + w_base / 2, pad + h),
        (cx0 + w_tip / 2, pad), (cx0 - w_tip / 2, pad),
    ], fill=col_outer + (255,))
    ed.ellipse([cx0 - w_base / 2, pad + h - w_base / 2, cx0 + w_base / 2, pad + h + w_base / 2],
               fill=col_outer + (255,))
    ed.ellipse([cx0 - w_tip / 2, pad - w_tip / 2, cx0 + w_tip / 2, pad + w_tip / 2],
               fill=col_outer + (255,))
    # Inner ear patch (upper 2/3, narrower)
    iw_base, iw_tip = 8, 4
    ed.polygon([
        (cx0 - iw_base / 2, pad + h * 0.9), (cx0 + iw_base / 2, pad + h * 0.9),
        (cx0 + iw_tip / 2, pad + h * 0.15), (cx0 - iw_tip / 2, pad + h * 0.15),
    ], fill=col_inner + (255,))

    rotated = ear.rotate(-angle, resample=Image.BICUBIC, expand=True)
    rw, rh = rotated.size
    # Base of the (unrotated) ear sits at (cx0, pad+h); after rotation about
    # the image centre, recover where that anchor point lands.
    cx_un, cy_un = ear.size[0] / 2, ear.size[1] / 2
    ax, ay = cx0 - cx_un, (pad + h) - cy_un
    rad = math.radians(angle)
    rax = ax * math.cos(rad) - ay * math.sin(rad)
    ray = ax * math.sin(rad) + ay * math.cos(rad)
    anchor_x, anchor_y = rw / 2 + rax, rh / 2 + ray

    paste_x = int(base_x - anchor_x)
    paste_y = int(base_y - anchor_y)
    stage.paste(rotated, (paste_x, paste_y), rotated)


def _draw_bunny(stage, state, t):
    draw = ImageDraw.Draw(stage)
    body_col  = (235, 235, 232)
    outline   = (160, 160, 165)
    if state == "error":
        body_col = (235, 205, 205)

    cycle = 1.1
    if state in ("idle",):
        phase, height = 0.0, 0.0
    elif state in ("listening", "thinking"):
        phase = (t % (cycle * 2)) / (cycle * 2)
        height = 0.15 * abs(math.sin(phase * math.pi))
    else:
        phase = (t % cycle) / cycle
        height = abs(math.sin(phase * math.pi))

    base_r = 38
    squash = 1.0 - 0.3 * (1.0 - height) ** 3
    stretch = 1.0 + 0.10 * height ** 4
    ry = base_r * squash * stretch
    rx = base_r * (1.9 - squash)

    cx = W / 2
    if state == "error":
        cx += 6 * math.sin(t * 14)  # quick shake
    elif state in ("listening", "thinking"):
        cx += 6 * math.sin(t * 0.9)

    body_bottom = _GROUND_Y - 6
    cy = body_bottom - ry * (0.3 + 0.7 * height * 1.3)
    cy = min(cy, body_bottom - ry * 0.3)

    # Shadow
    shadow_w = base_r * (1.5 - 0.5 * height)
    shadow_v = int(70 + 70 * height)
    draw.ellipse([cx - shadow_w, body_bottom - 3, cx + shadow_w, body_bottom + 6],
                 fill=(shadow_v, shadow_v, shadow_v))

    # Droop amount per state (0 = perked straight up, 1 = fully drooped)
    droop = {
        "idle": 0.85, "ready": 0.0, "listening": 0.0,
        "thinking": 0.3, "speaking": 0.05, "error": 0.7,
    }.get(state, 0.2)

    ear_base_y = cy - ry * 0.85
    ear_len = 46
    ear_wiggle = 6 * math.sin(t * 5) if state == "speaking" else 0
    left_tilt  = -22 + ear_wiggle + (10 * math.sin(t * 0.9) if state == "thinking" else 0)
    right_tilt =  22 - ear_wiggle - (6 if state == "listening" else 0)  # cocked ear

    _draw_ear(stage, cx - rx * 0.35, ear_base_y, left_tilt, ear_len, droop,
              body_col, (235, 190, 200))
    _draw_ear(stage, cx + rx * 0.35, ear_base_y, right_tilt,
              ear_len * (0.7 if state == "listening" else 1.0),
              droop * (0.3 if state == "listening" else 1.0),
              body_col, (235, 190, 200))

    # Head/body
    draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=body_col, outline=outline, width=3)

    # Cheeks
    cheek_y = cy + ry * 0.25
    draw.ellipse([cx - rx * 0.85, cheek_y - 8, cx - rx * 0.55, cheek_y + 8], fill=(250, 225, 225))
    draw.ellipse([cx + rx * 0.55, cheek_y - 8, cx + rx * 0.85, cheek_y + 8], fill=(250, 225, 225))

    # Eyes
    eye_y = cy - ry * 0.1
    blink = (state != "error") and (t % 3.2) < 0.12
    for ex in (cx - rx * 0.3, cx + rx * 0.3):
        if state == "idle":
            draw.arc([ex - 8, eye_y - 4, ex + 8, eye_y + 6], start=10, end=170,
                      fill=(40, 30, 20), width=2)
        elif state == "error":
            draw.line([ex - 6, eye_y - 6, ex + 6, eye_y + 6], fill=(120, 20, 20), width=3)
            draw.line([ex + 6, eye_y - 6, ex - 6, eye_y + 6], fill=(120, 20, 20), width=3)
        elif blink:
            draw.line([ex - 7, eye_y, ex + 7, eye_y], fill=(30, 20, 10), width=3)
        elif state == "thinking":
            draw.ellipse([ex - 8, eye_y - 9, ex + 8, eye_y + 5], fill=(255, 255, 255))
            draw.ellipse([ex - 3, eye_y - 9, ex + 3, eye_y - 3], fill=(20, 15, 10))
        else:
            wide = 1.15 if state == "listening" else 1.0
            draw.ellipse([ex - 8 * wide, eye_y - 9 * wide, ex + 8 * wide, eye_y + 9 * wide],
                         fill=(255, 255, 255))
            draw.ellipse([ex - 3, eye_y - 3, ex + 3, eye_y + 3], fill=(20, 15, 10))

    # Nose
    nose_y = cy + ry * 0.18
    draw.ellipse([cx - 5, nose_y - 4, cx + 5, nose_y + 4], fill=(230, 130, 140))

    # Mouth + buck teeth
    mouth_y = cy + ry * 0.4
    talking = state == "speaking" and (t % 0.4) < 0.22
    if state == "error":
        draw.arc([cx - 12, mouth_y - 4, cx + 12, mouth_y + 10], start=200, end=340,
                  fill=(120, 20, 20), width=3)
    elif talking or state == "listening":
        draw.ellipse([cx - 9, mouth_y - 3, cx + 9, mouth_y + 10], fill=(140, 60, 60))
        draw.rectangle([cx - 6, mouth_y - 2, cx - 1, mouth_y + 5], fill=(255, 255, 250))
        draw.rectangle([cx + 1, mouth_y - 2, cx + 6, mouth_y + 5], fill=(255, 255, 250))
    else:
        draw.arc([cx - 12, mouth_y - 6, cx + 12, mouth_y + 8], start=15, end=165,
                  fill=(120, 60, 50), width=2)
        draw.rectangle([cx - 5, mouth_y - 1, cx, mouth_y + 6], fill=(255, 255, 250),
                       outline=(200, 200, 195))
        draw.rectangle([cx, mouth_y - 1, cx + 5, mouth_y + 6], fill=(255, 255, 250),
                       outline=(200, 200, 195))

    # Whiskers
    for side in (-1, 1):
        wx = cx + side * rx * 0.75
        for dy in (-4, 3, 10):
            draw.line([wx, nose_y + dy, wx + side * 22, nose_y + dy - 3],
                      fill=(210, 210, 210), width=1)

    # Feet, only near ground contact
    if height < 0.25 and state not in ("listening", "thinking", "idle"):
        spread = rx * 0.5
        for fx in (cx - spread, cx + spread):
            draw.ellipse([fx - 8, body_bottom - 7, fx + 8, body_bottom + 4], fill=(220, 220, 218))


# ── Public API ───────────────────────────────────────────────────────────────

def draw_frame(state: str, caption: str, t: float, batt=None) -> Image.Image:
    """
    Render one 240x280 frame.

    Parameters
    ----------
    state   : "idle" | "ready" | "listening" | "thinking" | "speaking" | "error"
    caption : full reply text shown above the ground line
    t       : time.time() — drives all animation
    batt    : (level: float|None, charging: bool) or None
    """
    col = _STATE_COLORS.get(state, (120, 120, 120))
    tint = (120, 30, 30) if state == "error" else None

    sky = _sky_gradient(tint)
    img = Image.new("RGB", (W, H), (18, 20, 26))
    img.paste(sky, (0, _STAGE_TOP))
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, _STAGE_TOP + _GROUND_Y - 20, W, H], fill=(90, 170, 90))
    draw.line([0, _STAGE_TOP + _GROUND_Y - 20, W, _STAGE_TOP + _GROUND_Y - 20],
              fill=(70, 140, 70), width=2)

    # _draw_bunny works in stage-local coordinates (0..W, 0.._GROUND_Y); render
    # it on its own black canvas, then composite onto the final image shifted
    # down by _STAGE_TOP, using "non-black" as the opacity mask.
    stage = Image.new("RGB", (W, _GROUND_Y), (0, 0, 0))
    _draw_bunny(stage, state, t)
    img.paste(stage, (0, _STAGE_TOP), _make_alpha(stage))

    _draw_header(draw, state, col, batt)
    _draw_caption(draw, caption)

    return img


def _make_alpha(stage_img):
    """Alpha mask: anything not pure black in the stage render is opaque."""
    l = stage_img.convert("L")
    return l.point(lambda p: 255 if p > 0 else 0)
