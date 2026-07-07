#!/usr/bin/env python3
"""Draw geometric shapes on the Whisplay LCD to sanity-check the display.

On the Pi (with the Whisplay board attached) this pushes each frame to the
240x280 LCD over SPI. On any machine it also saves PNG previews to
zeev/previews/shapes_*.png so the frames can be reviewed without hardware.

Usage:
    python3 zeev/shapes_test.py            # push to LCD + save previews, Enter to advance
    python3 zeev/shapes_test.py --no-lcd   # save previews only
    python3 zeev/shapes_test.py --auto     # push to LCD, advance every 15s (non-interactive)
    python3 zeev/shapes_test.py --anim [SECONDS]   # animated bouncing ball + rotating
                                                    # polygon on the LCD (default 20s)
    python3 zeev/shapes_test.py --plasma [SECONDS] # full-frame numpy plasma/interference
                                                    # effect, no per-pixel Python loop
                                                    # (default 20s)
    python3 zeev/shapes_test.py --cartoon [SECONDS]  # bouncing blob character with
                                                      # squash-and-stretch, blinking,
                                                      # drifting clouds (default 20s)
    python3 zeev/shapes_test.py --psychedelic [SECONDS]  # kaleidoscopic rainbow
                                                          # swirl, numpy-vectorized
                                                          # (default 20s)
    python3 zeev/shapes_test.py --liquid [SECONDS] # liquid light-show: morphing
                                                    # color metaballs (default 20s)
    python3 zeev/shapes_test.py --tunnel [SECONDS] # zooming rainbow tunnel/vortex
                                                    # (default 20s)
    python3 zeev/shapes_test.py --matrix [SECONDS] # falling green digital rain
                                                    # (default 20s)
    python3 zeev/shapes_test.py --fire [SECONDS]   # classic demoscene fire effect
                                                    # (default 20s)
"""

import math
import random
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE    = Path(__file__).parent
OUT_DIR = HERE / "previews"
OUT_DIR.mkdir(exist_ok=True)

W, H = 240, 280
BG   = (10, 12, 18)


def frame_polygons():
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 110, 90], outline=(230, 80, 80), width=4)
    draw.rectangle([130, 10, 230, 90], fill=(60, 140, 230))
    draw.ellipse([10, 110, 110, 190], outline=(80, 220, 120), width=4)
    draw.ellipse([130, 110, 230, 190], fill=(220, 190, 60))

    cx, cy, r = 60, 240, 45
    tri = [(cx, cy - r), (cx - r * 0.87, cy + r * 0.5), (cx + r * 0.87, cy + r * 0.5)]
    draw.polygon(tri, outline=(200, 100, 230), width=4)

    cx2 = 180
    hexagon = [
        (cx2 + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
        for a in range(0, 360, 60)
    ]
    draw.polygon(hexagon, fill=(240, 140, 40))
    return img


def frame_lines_angles():
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    for i, x in enumerate(range(10, W - 10, 20)):
        draw.line([x, 10, x, H - 10], fill=(40 + i * 6 % 200, 100, 220), width=2)
    draw.line([10, H // 2, W - 10, H // 2], fill=(230, 230, 230), width=3)
    draw.arc([20, 20, W - 20, H - 20], start=0, end=270, fill=(90, 220, 200), width=5)
    return img


def frame_concentric():
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    cx, cy = W // 2, H // 2
    colors = [(230, 80, 80), (240, 160, 40), (230, 220, 60),
              (80, 220, 120), (60, 160, 230), (170, 90, 230)]
    for i, col in enumerate(colors):
        r = 130 - i * 20
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=4)
    return img


FRAMES = {
    "polygons":  frame_polygons,
    "lines":     frame_lines_angles,
    "concentric": frame_concentric,
}


def hsv_to_rgb(h, s, v):
    i = int(h * 6)
    f = h * 6 - i
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i % 6]
    return int(r * 255), int(g * 255), int(b * 255)


# Ball state carried between frames (simple physics, no wall collisions logic
# beyond reflecting velocity at the bounding box).
_ball = {"x": 60.0, "y": 60.0, "vx": 95.0, "vy": 70.0, "r": 16}


def frame_anim(t, dt):
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Rotating, color-cycling polygon (hexagon) centred in the lower half
    cx, cy, r = W // 2, 195, 55
    sides = 6
    spin  = t * 90  # deg/sec
    col   = hsv_to_rgb((t * 0.15) % 1.0, 0.75, 1.0)
    pts = [
        (cx + r * math.cos(math.radians(spin + a)),
         cy + r * math.sin(math.radians(spin + a)))
        for a in range(0, 360, 360 // sides)
    ]
    draw.polygon(pts, outline=col, width=4)

    # Small marker rotating around the polygon to sell the spin direction
    mx = cx + (r + 14) * math.cos(math.radians(spin))
    my = cy + (r + 14) * math.sin(math.radians(spin))
    draw.ellipse([mx - 5, my - 5, mx + 5, my + 5], fill=col)

    # Bouncing ball in the upper region, reflecting off the LCD edges
    b = _ball
    b["x"] += b["vx"] * dt
    b["y"] += b["vy"] * dt
    top, bottom = b["r"], 130
    left, right = b["r"], W - b["r"]
    if b["x"] < left or b["x"] > right:
        b["vx"] *= -1
        b["x"] = max(left, min(right, b["x"]))
    if b["y"] < top or b["y"] > bottom:
        b["vy"] *= -1
        b["y"] = max(top, min(bottom, b["y"]))
    ball_col = hsv_to_rgb((t * 0.3 + 0.5) % 1.0, 0.8, 1.0)
    draw.ellipse([b["x"] - b["r"], b["y"] - b["r"], b["x"] + b["r"], b["y"] + b["r"]],
                 fill=ball_col)

    return img


# ── Cartoon: bouncing blob with squash-and-stretch, blink, drifting clouds ──

_SKY_TOP    = (120, 190, 235)
_SKY_BOTTOM = (200, 230, 245)
_GROUND_Y   = 250
_CLOUDS     = [(30, 40, 30), (140, 60, 22), (200, 100, 26)]  # (x0, y, radius)


def _sky_gradient():
    img = Image.new("RGB", (W, H), _SKY_BOTTOM)
    px = img.load()
    for y in range(_GROUND_Y):
        f = y / _GROUND_Y
        col = tuple(int(_SKY_TOP[i] + (_SKY_BOTTOM[i] - _SKY_TOP[i]) * f) for i in range(3))
        for x in range(W):
            px[x, y] = col
    return img


_SKY_BASE = None  # built lazily so --no-lcd/preview-only runs still work headless


def frame_cartoon(t):
    global _SKY_BASE
    if _SKY_BASE is None:
        _SKY_BASE = _sky_gradient()
    img = _SKY_BASE.copy()
    draw = ImageDraw.Draw(img)

    # Ground
    draw.rectangle([0, _GROUND_Y, W, H], fill=(90, 170, 90))
    draw.line([0, _GROUND_Y, W, _GROUND_Y], fill=(70, 140, 70), width=2)

    # Drifting clouds, wrapping around screen width
    for cx0, cy, r in _CLOUDS:
        cx = (cx0 + t * 12) % (W + 80) - 40
        draw.ellipse([cx - r, cy - r * 0.6, cx + r, cy + r * 0.6], fill=(245, 248, 252))
        draw.ellipse([cx - r * 0.6, cy - r * 0.9, cx + r * 0.6, cy + r * 0.3], fill=(245, 248, 252))

    # ── Bouncing blob character (squash-and-stretch bounce cycle) ──────────
    cycle = 1.1
    phase = (t % cycle) / cycle          # 0..1 across one bounce
    height = abs(math.sin(phase * math.pi))  # 0 at ground, 1 at apex
    base_r = 42
    cx = W / 2 + 18 * math.sin(t * 0.7)  # gentle side-to-side drift

    # Squash near ground, stretch near apex (classic 12-principles bounce)
    squash = 1.0 - 0.35 * (1.0 - height) ** 3   # vertical squish factor near ground
    stretch = 1.0 + 0.12 * height ** 4          # slight vertical stretch at apex
    ry = base_r * squash * stretch
    rx = base_r * (2.0 - squash)                # widen when squashed, matching volume

    body_bottom = _GROUND_Y - 2
    cy = body_bottom - ry * (0.3 + 0.7 * height * 1.4)
    cy = min(cy, body_bottom - ry * 0.3)

    # Shadow — shrinks/lightens as the blob rises
    shadow_w = base_r * (1.6 - 0.6 * height)
    shadow_col = tuple(int(60 + 80 * height) for _ in range(3))
    draw.ellipse([cx - shadow_w, body_bottom - 4, cx + shadow_w, body_bottom + 6],
                 fill=shadow_col)

    body_col = (240, 140, 40)
    draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=body_col, outline=(180, 90, 10), width=3)

    # Blink: closed for a short beat every 2.6s
    blink = (t % 2.6) < 0.12
    eye_y = cy - ry * 0.15
    for ex in (cx - rx * 0.35, cx + rx * 0.35):
        if blink:
            draw.line([ex - 7, eye_y, ex + 7, eye_y], fill=(30, 20, 10), width=3)
        else:
            draw.ellipse([ex - 8, eye_y - 9, ex + 8, eye_y + 9], fill=(255, 255, 255))
            draw.ellipse([ex - 3, eye_y - 3, ex + 3, eye_y + 3], fill=(20, 15, 10))

    # Mouth: wider "oh" near the apex (mid-air excitement), smile on the ground
    mouth_y = cy + ry * 0.35
    if height > 0.55:
        draw.ellipse([cx - 10, mouth_y - 6, cx + 10, mouth_y + 10], fill=(120, 40, 40))
    else:
        draw.arc([cx - 14, mouth_y - 8, cx + 14, mouth_y + 12], start=15, end=165,
                  fill=(120, 40, 40), width=3)

    # Little feet, visible mainly during ground contact
    if height < 0.3:
        foot_spread = rx * 0.55
        for fx in (cx - foot_spread, cx + foot_spread):
            draw.ellipse([fx - 9, body_bottom - 8, fx + 9, body_bottom + 4], fill=(200, 110, 20))

    return img


def run_cartoon(board, duration):
    print(f"  cartoon for {duration:.0f}s ({'LCD' if board else 'preview only'})...")
    start = time.time()
    frame_count = 0
    saved_preview = False
    while True:
        now = time.time()
        t = now - start
        if t > duration:
            break
        img = frame_cartoon(t)
        if not saved_preview and t > 0.5:
            img.save(OUT_DIR / "shapes_cartoon.png")
            saved_preview = True
        if board is not None:
            push_to_lcd(board, img)
        frame_count += 1
    elapsed = time.time() - start
    print(f"  rendered {frame_count} frames in {elapsed:.1f}s ({frame_count / elapsed:.1f} fps)")


def run_animation(board, duration):
    print(f"  animating for {duration:.0f}s ({'LCD' if board else 'preview only'})...")
    start = time.time()
    last  = start
    frame_count = 0
    saved_preview = False
    while True:
        now = time.time()
        t   = now - start
        if t > duration:
            break
        dt = now - last
        last = now
        img = frame_anim(t, dt)
        if not saved_preview and t > 0.5:
            img.save(OUT_DIR / "shapes_anim.png")
            saved_preview = True
        if board is not None:
            push_to_lcd(board, img)
        frame_count += 1
        time.sleep(max(0, (1 / 15) - (time.time() - now)))
    print(f"  rendered {frame_count} frames")


# ── Plasma: fully vectorized, no per-pixel Python loop anywhere ─────────────

_YY, _XX = np.mgrid[0:H, 0:W].astype(np.float32)
_CX, _CY = W / 2, H / 2
_DIST = np.sqrt((_XX - _CX) ** 2 + (_YY - _CY) ** 2)


def hsv_to_rgb_arr(h, s, v):
    """Vectorized HSV->RGB. h, s, v are float arrays in [0, 1]. Returns uint8 (..., 3)."""
    i = np.floor(h * 6.0).astype(np.int32) % 6
    f = h * 6.0 - np.floor(h * 6.0)
    p = v * (1 - s)
    q = v * (1 - f * s)
    t_ = v * (1 - (1 - f) * s)

    r = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [v, q, p, p, t_, v])
    g = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [t_, v, v, q, p, p])
    b = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [p, p, t_, v, v, q])

    rgb = np.stack([r, g, b], axis=-1)
    return np.clip(rgb * 255, 0, 255).astype(np.uint8)


def frame_plasma(t):
    """Classic demoscene plasma: sum of moving sine fields -> hue, with a
    breathing radial glow driving brightness. All array ops, no Python loop
    over pixels — cheap enough to run in real time on a Pi Zero 2W.
    """
    wave = (
        np.sin((_XX + t * 40) / 22.0)
        + np.sin((_YY - t * 30) / 26.0)
        + np.sin((_XX + _YY + t * 50) / 30.0)
        + np.sin(_DIST / 14.0 - t * 2.4)
    )
    hue = (wave / 4.0 + 1.0) / 2.0  # normalize to [0, 1]
    hue = (hue + t * 0.05) % 1.0    # slow overall color drift

    glow = 0.55 + 0.45 * np.sin(_DIST / 20.0 - t * 1.6)
    val  = np.clip(0.55 + 0.45 * glow, 0.0, 1.0)
    sat  = np.full_like(hue, 0.95)

    return hsv_to_rgb_arr(hue, sat, val)


_THETA = np.arctan2(_YY - _CY, _XX - _CX)  # [-pi, pi]
_KAL_SEGMENTS = 10


def frame_psychedelic(t):
    """Kaleidoscopic prism swirl: mirrored angular symmetry + a radius-twist,
    rainbow hue cycling, pulsing concentric rings. All vectorized numpy — an
    original trippy-concert-visual design, not a reproduction of any
    particular album art.
    """
    # Swirl: rotate more near the centre, less at the edge, drifting over time
    twist = 2.6 * np.sin(t * 0.5) + 180.0 / (_DIST + 18.0)
    theta = _THETA + twist + t * 0.6

    # Fold into N mirrored wedges -> kaleidoscope symmetry
    wedge = (2 * np.pi) / _KAL_SEGMENTS
    folded = np.abs(((theta % wedge) - wedge / 2))

    # Hue driven by wedge angle + radius ripple + slow global drift
    hue = (folded / (wedge / 2) * 0.5
           + np.sin(_DIST / 16.0 - t * 3.0) * 0.15
           + t * 0.08) % 1.0

    # Pulsing concentric rings for brightness -> prism/strobe feel
    rings = 0.5 + 0.5 * np.sin(_DIST / 11.0 - t * 4.0)
    val = np.clip(0.35 + 0.65 * rings, 0.0, 1.0)
    sat = np.full_like(hue, 1.0)

    return hsv_to_rgb_arr(hue, sat, val)


def run_psychedelic(board, duration):
    print(f"  psychedelic for {duration:.0f}s ({'LCD' if board else 'preview only'})...")
    start = time.time()
    frame_count = 0
    saved_preview = False
    while True:
        now = time.time()
        t = now - start
        if t > duration:
            break
        arr = frame_psychedelic(t)
        if not saved_preview and t > 0.3:
            Image.fromarray(arr, "RGB").save(OUT_DIR / "shapes_psychedelic.png")
            saved_preview = True
        if board is not None:
            push_arr_to_lcd(board, arr)
        frame_count += 1
    elapsed = time.time() - start
    print(f"  rendered {frame_count} frames in {elapsed:.1f}s ({frame_count / elapsed:.1f} fps)")


# ── Matrix: falling green digital rain ──────────────────────────────────────

_MATRIX_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
_CELL_W, _CELL_H = 12, 14
_MATRIX_COLS = W // _CELL_W
_MATRIX_ROWS = H // _CELL_H
_MATRIX_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ!?$%&#@*+=/\\"
_TRAIL_LEN = 12

_matrix_font = None
_matrix_state = None  # lazily built: list of dicts per column


def _matrix_init():
    global _matrix_font, _matrix_state
    _matrix_font = _load_matrix_font()
    _matrix_state = []
    for _ in range(_MATRIX_COLS):
        _matrix_state.append({
            "head": random.uniform(-_TRAIL_LEN, _MATRIX_ROWS),
            "speed": random.uniform(6.0, 16.0),  # rows/sec
            "chars": [random.choice(_MATRIX_CHARS) for _ in range(_MATRIX_ROWS + _TRAIL_LEN)],
        })


def _load_matrix_font():
    try:
        return ImageFont.truetype(_MATRIX_FONT_PATH, 13)
    except Exception:
        return ImageFont.load_default()


def frame_matrix(dt):
    """Falling green character columns with fading trails — an original take
    on the classic 'digital rain' code-cascade look (in the spirit of many
    open-source cmatrix-style implementations, not any specific film asset).
    """
    global _matrix_state
    if _matrix_state is None:
        _matrix_init()

    img = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    for col_idx, col in enumerate(_matrix_state):
        col["head"] += col["speed"] * dt
        if col["head"] - _TRAIL_LEN > _MATRIX_ROWS:
            col["head"] = random.uniform(-_TRAIL_LEN, -1)
            col["speed"] = random.uniform(6.0, 16.0)

        x = col_idx * _CELL_W
        head_row = int(col["head"])
        for offset in range(_TRAIL_LEN):
            row = head_row - offset
            if row < 0 or row >= _MATRIX_ROWS:
                continue
            if random.random() < 0.04:  # occasional glyph flicker
                col["chars"][row % len(col["chars"])] = random.choice(_MATRIX_CHARS)
            brightness = 1.0 - offset / _TRAIL_LEN
            if offset == 0:
                color = (200, 255, 210)  # bright near-white head
            else:
                g = int(60 + 160 * brightness)
                color = (10, g, 40)
            ch = col["chars"][row % len(col["chars"])]
            draw.text((x, row * _CELL_H), ch, font=_matrix_font, fill=color)

    return img


def run_matrix(board, duration):
    print(f"  matrix for {duration:.0f}s ({'LCD' if board else 'preview only'})...")
    start = time.time()
    last = start
    frame_count = 0
    saved_preview = False
    while True:
        now = time.time()
        t = now - start
        if t > duration:
            break
        dt = now - last
        last = now
        img = frame_matrix(dt)
        if not saved_preview and t > 0.5:
            img.save(OUT_DIR / "shapes_matrix.png")
            saved_preview = True
        if board is not None:
            push_to_lcd(board, img)
        frame_count += 1
    elapsed = time.time() - start
    print(f"  rendered {frame_count} frames in {elapsed:.1f}s ({frame_count / elapsed:.1f} fps)")


# ── Fire: classic demoscene propagating-heat effect ─────────────────────────

_FIRE_W, _FIRE_H = 60, 70   # low-res buffer, upscaled to the full LCD each frame
_fire_buf = None
_fire_palette = None


def _build_fire_palette():
    stops = [
        (0,   (0, 0, 0)),
        (60,  (110, 0, 0)),
        (120, (255, 60, 0)),
        (180, (255, 180, 0)),
        (255, (255, 255, 220)),
    ]
    pal = np.zeros((256, 3), dtype=np.uint8)
    for (x0, c0), (x1, c1) in zip(stops, stops[1:]):
        for x in range(x0, x1 + 1):
            f = (x - x0) / (x1 - x0)
            pal[x] = [int(c0[i] + (c1[i] - c0[i]) * f) for i in range(3)]
    return pal


def frame_fire():
    """Propagating-heat fire: each cell averages the row below plus random
    cooling, mapped through a black->red->orange->yellow->white palette.
    Computed at low res and upscaled for smooth flames on limited hardware.
    """
    global _fire_buf, _fire_palette
    if _fire_buf is None:
        _fire_buf = np.zeros((_FIRE_H, _FIRE_W), dtype=np.float32)
        _fire_palette = _build_fire_palette()

    # Seed the bottom rows — mostly hot, with random gaps for flicker
    gaps = np.random.random(_FIRE_W) < 0.9
    _fire_buf[_FIRE_H - 1] = np.where(gaps, np.random.uniform(200, 255, _FIRE_W), 0)
    _fire_buf[_FIRE_H - 2] = np.where(gaps, np.random.uniform(160, 230, _FIRE_W), 0)

    # Propagate upward: each cell = avg of the row below (self + neighbors) - decay
    for y in range(_FIRE_H - 2, -1, -1):
        below = _fire_buf[y + 1]
        left = np.roll(below, 1)
        right = np.roll(below, -1)
        decay = np.random.uniform(0.0, 5.5, _FIRE_W)
        _fire_buf[y] = np.clip((left + below + right) / 3.0 - decay, 0, 255)

    idx = _fire_buf.astype(np.uint8)
    rgb_small = _fire_palette[idx]
    small_img = Image.fromarray(rgb_small, "RGB")
    return small_img.resize((W, H), Image.BILINEAR)


def run_fire(board, duration):
    print(f"  fire for {duration:.0f}s ({'LCD' if board else 'preview only'})...")
    start = time.time()
    frame_count = 0
    saved_preview = False
    while True:
        now = time.time()
        t = now - start
        if t > duration:
            break
        img = frame_fire()
        if not saved_preview and t > 0.5:
            img.save(OUT_DIR / "shapes_fire.png")
            saved_preview = True
        if board is not None:
            push_to_lcd(board, img)
        frame_count += 1
    elapsed = time.time() - start
    print(f"  rendered {frame_count} frames in {elapsed:.1f}s ({frame_count / elapsed:.1f} fps)")


# Moving metaball params: (amp_x, amp_y, speed, phase, hue0, hue_speed, sigma)
_BLOBS = [
    (70, 90, 0.55, 0.0, 0.00, 0.03, 46),
    (85, 60, 0.42, 2.1, 0.18, 0.04, 40),
    (55, 100, 0.65, 4.2, 0.38, 0.02, 52),
    (95, 75, 0.35, 1.3, 0.58, 0.05, 44),
    (65, 55, 0.50, 5.0, 0.78, 0.03, 38),
]


def frame_liquid(t):
    """Liquid light-show: soft moving color blobs (metaballs) that merge and
    separate, in the spirit of the oil-and-dye projections used at 60s/70s
    psychedelic concerts. All vectorized numpy.
    """
    total_w = np.zeros((H, W), dtype=np.float32)
    accum = np.zeros((H, W, 3), dtype=np.float32)

    for amp_x, amp_y, speed, phase, hue0, hue_speed, sigma in _BLOBS:
        bx = _CX + amp_x * np.sin(t * speed + phase)
        by = _CY + amp_y * np.cos(t * speed * 1.3 + phase)
        d2 = (_XX - bx) ** 2 + (_YY - by) ** 2
        weight = np.exp(-d2 / (2 * sigma * sigma))
        hue = (hue0 + t * hue_speed) % 1.0
        col = np.array(hsv_to_rgb(hue, 0.9, 1.0), dtype=np.float32)
        accum += weight[..., None] * col
        total_w += weight

    safe_w = np.clip(total_w, 1e-3, None)
    rgb = accum / safe_w[..., None]

    # Glow boost where blobs overlap, dark background elsewhere (projector-on-
    # black-screen contrast)
    glow = np.clip(total_w, 0.0, 1.4) / 1.4
    rgb = rgb * glow[..., None]

    return np.clip(rgb, 0, 255).astype(np.uint8)


def run_liquid(board, duration):
    print(f"  liquid light show for {duration:.0f}s ({'LCD' if board else 'preview only'})...")
    start = time.time()
    frame_count = 0
    saved_preview = False
    while True:
        now = time.time()
        t = now - start
        if t > duration:
            break
        arr = frame_liquid(t)
        if not saved_preview and t > 0.3:
            Image.fromarray(arr, "RGB").save(OUT_DIR / "shapes_liquid.png")
            saved_preview = True
        if board is not None:
            push_arr_to_lcd(board, arr)
        frame_count += 1
    elapsed = time.time() - start
    print(f"  rendered {frame_count} frames in {elapsed:.1f}s ({frame_count / elapsed:.1f} fps)")


_LOG_DIST = np.log(_DIST + 1.0)


def frame_tunnel(t):
    """Zooming rainbow tunnel: concentric rings receding toward the centre with
    radial spokes, log-radius mapping for a classic wormhole/vortex feel.
    """
    rings = np.sin(_LOG_DIST * 9.0 - t * 5.0)
    spokes = np.sin(_THETA * 8.0 + t * 1.2)

    hue = (_LOG_DIST * 0.35 - t * 0.4 + spokes * 0.05) % 1.0
    val = np.clip(0.62 + 0.28 * rings + 0.1 * spokes, 0.2, 1.0)
    sat = np.full_like(hue, 1.0)

    return hsv_to_rgb_arr(hue, sat, val)


def run_tunnel(board, duration):
    print(f"  tunnel for {duration:.0f}s ({'LCD' if board else 'preview only'})...")
    start = time.time()
    frame_count = 0
    saved_preview = False
    while True:
        now = time.time()
        t = now - start
        if t > duration:
            break
        arr = frame_tunnel(t)
        if not saved_preview and t > 0.3:
            Image.fromarray(arr, "RGB").save(OUT_DIR / "shapes_tunnel.png")
            saved_preview = True
        if board is not None:
            push_arr_to_lcd(board, arr)
        frame_count += 1
    elapsed = time.time() - start
    print(f"  rendered {frame_count} frames in {elapsed:.1f}s ({frame_count / elapsed:.1f} fps)")


def push_arr_to_lcd(board, arr_rgb_u8):
    """Push a (H, W, 3) uint8 RGB array straight to the LCD — no PIL round trip."""
    arr = arr_rgb_u8.astype(np.uint16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    buf = v.astype(">u2").tobytes()
    board.draw_image(0, 0, W, H, buf)


def run_plasma(board, duration):
    print(f"  plasma for {duration:.0f}s ({'LCD' if board else 'preview only'})...")
    start = time.time()
    frame_count = 0
    saved_preview = False
    while True:
        now = time.time()
        t = now - start
        if t > duration:
            break
        arr = frame_plasma(t)
        if not saved_preview and t > 0.3:
            Image.fromarray(arr, "RGB").save(OUT_DIR / "shapes_plasma.png")
            saved_preview = True
        if board is not None:
            push_arr_to_lcd(board, arr)
        frame_count += 1
    elapsed = time.time() - start
    print(f"  rendered {frame_count} frames in {elapsed:.1f}s ({frame_count / elapsed:.1f} fps)")


def push_to_lcd(board, img):
    arr = np.asarray(img.convert("RGB"), dtype=np.uint16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    buf = v.astype(">u2").tobytes()  # big-endian u16 == hi byte, lo byte per pixel
    board.draw_image(0, 0, W, H, buf)


def main():
    no_lcd = "--no-lcd" in sys.argv
    auto   = "--auto" in sys.argv
    anim   = "--anim" in sys.argv
    plasma = "--plasma" in sys.argv
    cartoon = "--cartoon" in sys.argv
    psychedelic = "--psychedelic" in sys.argv
    liquid = "--liquid" in sys.argv
    tunnel = "--tunnel" in sys.argv
    matrix = "--matrix" in sys.argv
    fire   = "--fire" in sys.argv

    board = None
    if not no_lcd:
        sys.path.insert(0, str(Path.home() / "Whisplay" / "runtime"))
        try:
            from whisplay import WhisplayBoard
            board = WhisplayBoard()
            spi_hz = 20_000_000
            if "--spi-hz" in sys.argv:
                spi_hz = int(sys.argv[sys.argv.index("--spi-hz") + 1])
            board.spi.max_speed_hz = spi_hz
            board._reset_lcd()
            board._init_display()
            board.set_backlight(100)
            print(f"  SPI clock: requested {spi_hz} Hz, negotiated {board.spi.max_speed_hz} Hz")
        except ImportError:
            print("Whisplay runtime not found — saving previews only.")

    if anim:
        idx = sys.argv.index("--anim")
        duration = 20.0
        if idx + 1 < len(sys.argv):
            try:
                duration = float(sys.argv[idx + 1])
            except ValueError:
                pass
        run_animation(board, duration)
        print(f"\nDone. Preview in {OUT_DIR}/shapes_anim.png")
        return

    if plasma:
        idx = sys.argv.index("--plasma")
        duration = 20.0
        if idx + 1 < len(sys.argv):
            try:
                duration = float(sys.argv[idx + 1])
            except ValueError:
                pass
        run_plasma(board, duration)
        print(f"\nDone. Preview in {OUT_DIR}/shapes_plasma.png")
        return

    if cartoon:
        idx = sys.argv.index("--cartoon")
        duration = 20.0
        if idx + 1 < len(sys.argv):
            try:
                duration = float(sys.argv[idx + 1])
            except ValueError:
                pass
        run_cartoon(board, duration)
        print(f"\nDone. Preview in {OUT_DIR}/shapes_cartoon.png")
        return

    if psychedelic:
        idx = sys.argv.index("--psychedelic")
        duration = 20.0
        if idx + 1 < len(sys.argv):
            try:
                duration = float(sys.argv[idx + 1])
            except ValueError:
                pass
        run_psychedelic(board, duration)
        print(f"\nDone. Preview in {OUT_DIR}/shapes_psychedelic.png")
        return

    if liquid:
        idx = sys.argv.index("--liquid")
        duration = 20.0
        if idx + 1 < len(sys.argv):
            try:
                duration = float(sys.argv[idx + 1])
            except ValueError:
                pass
        run_liquid(board, duration)
        print(f"\nDone. Preview in {OUT_DIR}/shapes_liquid.png")
        return

    if tunnel:
        idx = sys.argv.index("--tunnel")
        duration = 20.0
        if idx + 1 < len(sys.argv):
            try:
                duration = float(sys.argv[idx + 1])
            except ValueError:
                pass
        run_tunnel(board, duration)
        print(f"\nDone. Preview in {OUT_DIR}/shapes_tunnel.png")
        return

    if matrix:
        idx = sys.argv.index("--matrix")
        duration = 20.0
        if idx + 1 < len(sys.argv):
            try:
                duration = float(sys.argv[idx + 1])
            except ValueError:
                pass
        run_matrix(board, duration)
        print(f"\nDone. Preview in {OUT_DIR}/shapes_matrix.png")
        return

    if fire:
        idx = sys.argv.index("--fire")
        duration = 20.0
        if idx + 1 < len(sys.argv):
            try:
                duration = float(sys.argv[idx + 1])
            except ValueError:
                pass
        run_fire(board, duration)
        print(f"\nDone. Preview in {OUT_DIR}/shapes_fire.png")
        return

    for name, make_frame in FRAMES.items():
        img = make_frame()
        img.save(OUT_DIR / f"shapes_{name}.png")
        print(f"  saved previews/shapes_{name}.png")
        if board is not None:
            push_to_lcd(board, img)
            if auto:
                print(f"  pushed {name} to LCD — holding 15s")
                time.sleep(15)
            else:
                print(f"  pushed {name} to LCD")
                try:
                    input("  press Enter for next shape... ")
                except (EOFError, KeyboardInterrupt):
                    break

    print(f"\nDone. Previews in {OUT_DIR}/")


if __name__ == "__main__":
    main()
