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
"""

import math
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

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

    board = None
    if not no_lcd:
        sys.path.insert(0, str(Path.home() / "Whisplay" / "runtime"))
        try:
            from whisplay import WhisplayBoard
            board = WhisplayBoard()
            board.spi.max_speed_hz = 10_000_000
            board._reset_lcd()
            board._init_display()
            board.set_backlight(100)
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
