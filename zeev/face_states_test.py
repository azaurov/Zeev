#!/usr/bin/env python3
"""Cycle through every device-mode face state on the Whisplay LCD, using the
exact same renderer (face_aura.draw_frame) and push interval per state as
run_device_mode()'s _face_loop, to visually verify timing (aura pulse,
spoke motion, scroll) on real hardware.

Usage:
    python3 zeev/face_states_test.py [HOLD_SECONDS_PER_STATE]   # default 6s
"""

import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import face_aura

W, H = 240, 280

# Must match zeev.py run_device_mode()'s _FACE_INTERVAL exactly.
_FACE_INTERVAL = {
    "idle":      1 / 6,
    "ready":     1.0,
    "listening": 1 / 8,
    "thinking":  1 / 8,
    "speaking":  1 / 8,
    "error":     1.0,
}
STATES = ["idle", "ready", "listening", "thinking", "speaking", "error"]

CAPTION = ("The capital of France is Paris, one of the most visited cities "
           "in the world. It sits on the River Seine in northern France.")


def push_to_lcd(board, img):
    arr = np.asarray(img.convert("RGB"), dtype=np.uint16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    buf = v.astype(">u2").tobytes()
    board.draw_image(0, 0, W, H, buf)


def main():
    hold = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0

    sys.path.insert(0, str(Path.home() / "Whisplay" / "runtime"))
    from whisplay import WhisplayBoard
    board = WhisplayBoard()
    board.spi.max_speed_hz = 20_000_000
    board._reset_lcd()
    board._init_display()
    board.set_backlight(100)

    for state in STATES:
        print(f"  state={state}  interval={_FACE_INTERVAL[state]:.3f}s  holding {hold}s")
        start = time.time()
        while time.time() - start < hold:
            t = time.time()
            img = face_aura.draw_frame(state, CAPTION, t, batt=(72.0, False))
            push_to_lcd(board, img)
            time.sleep(_FACE_INTERVAL[state])

    print("\nDone cycling all states.")


if __name__ == "__main__":
    main()
