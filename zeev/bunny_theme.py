#!/usr/bin/env python3
"""Generate a short, original bouncy 'cartoon romp' jingle (plucked triangle-wave
arpeggio, not a reproduction of any existing theme song) as a WAV file, for use
alongside the bunny animation test.

Usage:
    python3 zeev/bunny_theme.py [output.wav]
"""

import struct
import sys
import wave
from pathlib import Path

import numpy as np

SR = 22050

# C major-ish bouncy arpeggio, two bars, comedic up-down run (Hz)
_NOTES = [
    523.25, 659.25, 783.99, 1046.50, 783.99, 659.25,   # C E G C6 G E
    587.33, 698.46, 880.00, 587.33, 440.00, 523.25,    # D F A D A3 C
    523.25, 659.25, 783.99, 1046.50, 1318.51, 1046.50,  # C E G C6 E6 C6
    783.99, 659.25, 523.25, 392.00, 440.00, 523.25,    # G E C G3 A C
]
_NOTE_DUR = 0.16


def _pluck(freq, dur, sr=SR):
    n = int(dur * sr)
    t = np.arange(n) / sr
    # Triangle wave for a soft marimba/pizzicato timbre
    wave_ = 2 * np.abs(2 * (t * freq - np.floor(t * freq + 0.5))) - 1
    # Quick attack, exponential decay envelope -> plucked feel
    env = np.exp(-t * 10.0)
    env[: int(0.003 * sr)] *= np.linspace(0, 1, int(0.003 * sr))
    return wave_ * env


def generate_theme(repeats=2):
    chunks = [_pluck(f, _NOTE_DUR) for f in _NOTES]
    one_pass = np.concatenate(chunks)
    full = np.tile(one_pass, repeats)
    full = full / np.max(np.abs(full)) * 0.85
    return (full * 32767).astype(np.int16)


def save_wav(path, samples, sr=SR):
    with wave.open(str(path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(struct.pack(f"<{len(samples)}h", *samples))


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "previews" / "bunny_theme.wav"
    out.parent.mkdir(exist_ok=True)
    samples = generate_theme()
    save_wav(out, samples)
    print(f"Saved {out} ({len(samples) / SR:.1f}s)")
