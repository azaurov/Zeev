#!/usr/bin/env python3
"""Draw geometric shapes on the Whisplay LCD to sanity-check the display.

On the Pi (with the Whisplay board attached) this pushes each frame to the
240x280 LCD over SPI. On any machine it also saves PNG previews to
zeev/previews/shapes_*.png so the frames can be reviewed without hardware.

Usage:
    python3 zeev/shapes_test.py            # push to LCD + save previews, Enter to advance
    python3 zeev/shapes_test.py --no-lcd   # save previews only
    python3 zeev/shapes_test.py --auto     # push to LCD, advance every 15s (non-interactive)
"""

import math
import sys
import time
from pathlib import Path

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


def push_to_lcd(board, img):
    pixels = list(img.convert("RGB").getdata())
    buf = bytearray(len(pixels) * 2)
    for i, (r, g, b) in enumerate(pixels):
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        buf[i * 2]     = v >> 8
        buf[i * 2 + 1] = v & 0xFF
    board.draw_image(0, 0, W, H, bytes(buf))


def main():
    no_lcd = "--no-lcd" in sys.argv
    auto   = "--auto" in sys.argv

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
