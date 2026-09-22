"""A short rolling buffer of what the living-room mic heard, in RAM.

Written by device mode's wake listener (the only process that may hold the
WM8960 capture device -- it is a single hw subdevice, so a second `arecord`
fails "device busy"), read by watch_server's `listen` command so the dog
calmer in ~/troubleshooting/wyze-dog-caller can ask "was that barking?"
about a Wyze sound event without taking the mic away from wake detection.

Chunks of raw S16LE 16 kHz mono PCM land in RING_DIR (tmpfs, so nothing
survives a reboot and nothing touches the SD card), named by the epoch
milliseconds of their first sample. Only the last KEEP_SECONDS are kept.

There are gaps by design: the wake listener releases the mic for every turn,
so Zeev's own replies are mostly absent. read_window() never stitches across
a gap silently -- it reports how many seconds it actually covered.

Writer failures are logged once and swallowed: this runs inside the wake
loop, and a full /dev/shm must never cost Alex his wake word.
"""
import io
import os
import time
import wave

RING_DIR = os.environ.get("ZEEV_MIC_RING_DIR", "/dev/shm/zeev_mic")
ENABLED = os.environ.get("ZEEV_MIC_RING", "1") != "0"
RATE = 16000
BYTES_PER_SEC = RATE * 2
CHUNK_SECONDS = 5
KEEP_SECONDS = 120
MAX_WINDOW_SECONDS = 60


class RingWriter:
    def __init__(self, ring_dir=None, chunk_seconds=CHUNK_SECONDS,
                 keep_seconds=KEEP_SECONDS, clock=time.time):
        self.dir = ring_dir or RING_DIR
        self.chunk_bytes = int(chunk_seconds * BYTES_PER_SEC)
        self.keep = keep_seconds
        self.clock = clock
        self.buf = bytearray()
        self.start = None
        self._failed = False

    def append(self, data):
        now = self.clock()
        if not self.buf:
            # The frame was captured over the last len/BPS seconds.
            self.start = now - len(data) / BYTES_PER_SEC
        elif abs((self.start + len(self.buf) / BYTES_PER_SEC) - (now - len(data) / BYTES_PER_SEC)) > 1.0:
            # The stream was released (a turn) and reopened: flush what we
            # have rather than pretend the two sides are contiguous.
            self.flush()
            self.start = now - len(data) / BYTES_PER_SEC
        self.buf += data
        if len(self.buf) >= self.chunk_bytes:
            self.flush()

    def flush(self):
        if not self.buf:
            return
        data, start = bytes(self.buf), self.start
        self.buf = bytearray()
        self.start = None
        try:
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            name = os.path.join(self.dir, f"{round(start * 1000)}.pcm")
            tmp = name + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, name)
            self._prune()
        except OSError as e:
            if not self._failed:
                print(f"[mic_ring] write failed (further failures silent): {e}", flush=True)
                self._failed = True

    def _prune(self):
        cutoff = self.clock() - self.keep
        for start, _end, path in _chunks(self.dir):
            if start < cutoff:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _chunks(ring_dir):
    """[(start, end, path)] sorted by start."""
    out = []
    try:
        names = os.listdir(ring_dir)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".pcm"):
            continue
        try:
            start = int(name[:-4]) / 1000
            path = os.path.join(ring_dir, name)
            end = start + os.path.getsize(path) / BYTES_PER_SEC
        except (ValueError, OSError):
            continue
        out.append((start, end, path))
    return sorted(out)


def read_window(start, end, ring_dir=None):
    """PCM the mic heard between epoch `start` and `end`.

    Returns (pcm_bytes, covered_seconds, latest_end). Chunks are joined in
    order; any gap between them is simply absent (covered_seconds says how
    much was really heard). latest_end is the newest audio on disk, so a
    caller can tell "the listener is paused" from "nothing overlapped"."""
    ring_dir = ring_dir or RING_DIR
    end = min(end, start + MAX_WINDOW_SECONDS)
    parts, covered, latest = [], 0.0, None
    for c_start, c_end, path in _chunks(ring_dir):
        latest = c_end if latest is None else max(latest, c_end)
        lo, hi = max(start, c_start), min(end, c_end)
        if hi <= lo:
            continue
        a = round((lo - c_start) * RATE) * 2
        b = round((hi - c_start) * RATE) * 2
        try:
            with open(path, "rb") as f:
                f.seek(a)
                parts.append(f.read(b - a))
        except OSError:
            continue
        covered += (b - a) / BYTES_PER_SEC
    return b"".join(parts), round(covered, 2), latest


def to_wav(pcm):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return out.getvalue()
