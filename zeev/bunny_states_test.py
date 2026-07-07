#!/usr/bin/env python3
"""Cycle through every device-mode face state using the bunny cartoon renderer
(face_cartoon.draw_frame) on the Whisplay LCD, at each state's real production
push interval, with an original cartoon-romp jingle playing in the background.

Usage:
    python3 zeev/bunny_states_test.py [HOLD_SECONDS_PER_STATE]   # default 6s
"""

import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import face_cartoon
import bunny_theme

W, H = 240, 280

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

    wav_path = HERE / "previews" / "bunny_theme.wav"
    if not wav_path.exists():
        bunny_theme.save_wav(wav_path, bunny_theme.generate_theme())
        print(f"  generated {wav_path}")

    sys.path.insert(0, str(Path.home() / "Whisplay" / "runtime"))
    from whisplay import WhisplayBoard
    board = WhisplayBoard()
    board.spi.max_speed_hz = 20_000_000
    board._reset_lcd()
    board._init_display()
    board.set_backlight(100)

    player = subprocess.Popen(
        ["aplay", "-q", str(wav_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("  theme playing...")

    for state in STATES:
        print(f"  state={state}  interval={_FACE_INTERVAL[state]:.3f}s  holding {hold}s")
        start = time.time()
        while time.time() - start < hold:
            t = time.time()
            img = face_cartoon.draw_frame(state, CAPTION, t, batt=(72.0, False))
            push_to_lcd(board, img)
            time.sleep(_FACE_INTERVAL[state])

    if player.poll() is None:
        player.terminate()
    print("\nDone cycling all states.")


if __name__ == "__main__":
    main()
