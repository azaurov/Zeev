"""
Shared fixtures for the Zeev TTS/voice regression suite.

zeev.py has module-level globals but no import-time side effects beyond
reading environment variables and defining functions.  We import it once
per session (pytest caches the module) and reset the relevant globals in
fixtures that need a clean slate.
"""
import importlib
import sys
import types
import threading
import pytest

# ---------------------------------------------------------------------------
# Import zeev without triggering any startup I/O
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def zeev():
    """Import zeev.py as a module.  Filesystem-reading init functions
    (init_tts, init_learning, zeev_cleanup) are NOT called here."""
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "zeev"))
    import zeev as _zeev
    return _zeev


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_wav(duration_s: float = 0.5, sample_rate: int = 22050) -> bytes:
    """Build a minimal valid 16-bit mono WAV (sine wave) of given duration."""
    import struct, math
    n_samples = int(sample_rate * duration_s)
    audio = b"".join(
        struct.pack("<h", int(32767 * math.sin(2 * math.pi * 440 * i / sample_rate)))
        for i in range(n_samples)
    )
    data_size = len(audio)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,        # chunk size
        1,         # PCM
        1,         # mono
        sample_rate,
        sample_rate * 2,  # byte rate
        2,         # block align
        16,        # bits per sample
        b"data",
        data_size,
    )
    return header + audio
