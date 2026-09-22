"""mic_ring (rolling living-room mic buffer) and watch_server's `listen`.

The dog calmer on bosgame decides "was that barking?" from these bytes, so
the tests pin that the audio handed back is the audio that was heard, at the
right offset -- not just that a WAV came back.
"""
import base64
import http.client
import io
import json
import struct
import sys
import threading
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "zeev"))

import mic_ring  # noqa: E402

BPS = mic_ring.BYTES_PER_SEC
FRAME = 2560  # the wake listener's 1280-sample read


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _tone(value, seconds):
    return struct.pack("<h", value) * int(seconds * mic_ring.RATE)


def _feed(writer, clock, pcm):
    """Hand `pcm` to the writer in real-time-paced wake-loop frames."""
    for i in range(0, len(pcm), FRAME):
        frame = pcm[i:i + FRAME]
        clock.t += len(frame) / BPS
        writer.append(frame)


def test_window_returns_the_audio_heard_at_that_time(tmp_path):
    clock = Clock(1000.0)
    w = mic_ring.RingWriter(str(tmp_path), clock=clock)
    # 10s of silence then 10s of a constant "loud" value: a window over the
    # second half must contain only the loud samples.
    _feed(w, clock, _tone(0, 10) + _tone(9000, 10))
    w.flush()
    pcm, covered, latest = mic_ring.read_window(1012.0, 1016.0, str(tmp_path))
    assert covered == pytest.approx(4.0, abs=0.01)
    assert len(pcm) == 4 * BPS
    assert set(struct.unpack(f"<{len(pcm) // 2}h", pcm)) == {9000}
    assert latest == pytest.approx(1020.0, abs=0.01)


def test_gap_is_not_stitched_over(tmp_path):
    clock = Clock(2000.0)
    w = mic_ring.RingWriter(str(tmp_path), clock=clock)
    _feed(w, clock, _tone(100, 3))
    w.gap()        # a turn: the wake listener released the mic...
    clock.t += 30  # ...for 30s
    _feed(w, clock, _tone(200, 3))
    w.flush()
    pcm, covered, _ = mic_ring.read_window(2000.0, 2040.0, str(tmp_path))
    assert covered == pytest.approx(6.0, abs=0.01)
    # Nothing was heard during the gap; audio from after it must not be
    # filed there (it would put barks at the wrong time).
    _pcm, covered_gap, _ = mic_ring.read_window(2004.0, 2032.0, str(tmp_path))
    assert covered_gap == 0
    # The second run is filed at its own time, not glued onto the first.
    pcm2, covered2, _ = mic_ring.read_window(2035.0, 2040.0, str(tmp_path))
    assert covered2 == pytest.approx(1.0, abs=0.01)
    assert set(struct.unpack(f"<{len(pcm2) // 2}h", pcm2)) == {200}


def test_bursty_reads_keep_true_timestamps_and_no_false_gaps(tmp_path):
    # The Pi's loop reads in bursts: several frames back to back (clock
    # barely moves), then a stall. Capture itself is continuous and real-time.
    clock = Clock(6000.0)
    w = mic_ring.RingWriter(str(tmp_path), clock=clock)
    pcm = _tone(0, 10) + _tone(8000, 10)
    frames = [pcm[i:i + FRAME] for i in range(0, len(pcm), FRAME)]
    captured = 6000.0
    for i, frame in enumerate(frames):
        captured += len(frame) / BPS
        lag = 1.5 if i % 20 < 15 else 0.0   # 15 late reads, then 5 prompt ones
        clock.t = captured + lag
        w.append(frame)
    w.flush()
    chunks = mic_ring._chunks(str(tmp_path))
    assert len(chunks) == 4  # 20s in 5s chunks: no burst-induced splits
    pcm_w, covered, _ = mic_ring.read_window(6012.0, 6016.0, str(tmp_path))
    assert covered == pytest.approx(4.0, abs=0.01)
    assert set(struct.unpack(f"<{len(pcm_w) // 2}h", pcm_w)) == {8000}


def test_release_closes_the_chunk():
    src = (Path(__file__).resolve().parent.parent / "zeev" / "zeev.py").read_text()
    release = src[src.index("        def _release():"):]
    release = release[:release.index("\n\n")]
    assert "mic_ring.gap()" in release


def test_old_audio_is_pruned(tmp_path):
    clock = Clock(5000.0)
    w = mic_ring.RingWriter(str(tmp_path), keep_seconds=20, clock=clock)
    _feed(w, clock, _tone(1, 60))
    w.flush()
    starts = [c[0] for c in mic_ring._chunks(str(tmp_path))]
    assert starts and min(starts) >= clock.t - 20 - mic_ring.CHUNK_SECONDS


def test_writer_never_raises_into_the_wake_loop(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a dir")
    w = mic_ring.RingWriter(str(blocker / "ring"), clock=Clock(1.0))
    w.append(_tone(1, 6))  # crosses a chunk boundary -> write fails
    w.flush()


def test_wake_loop_feeds_every_frame_to_the_ring():
    # Structural: the append must sit before the energy gate's `continue`,
    # or gated (quiet-then-bark) frames would never reach the buffer.
    src = (Path(__file__).resolve().parent.parent / "zeev" / "zeev.py").read_text()
    loop = src[src.index("frame = np.frombuffer(data, dtype=np.int16)"):]
    assert loop.index("mic_ring.append(data)") < loop.index("if OWW_ENERGY_GATE:")


@pytest.fixture
def watch_server(zeev, monkeypatch, tmp_path):
    monkeypatch.setattr(zeev, "ZEEV_WATCH_KEY", "test-secret")
    monkeypatch.setattr(mic_ring, "RING_DIR", str(tmp_path))
    import watch_server as ws

    server = ThreadingHTTPServer(("127.0.0.1", 0), ws._make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield tmp_path, server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _post(port, body, extra_headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json", "X-Zeev-Watch-Key": "test-secret"}
    headers.update(extra_headers or {})
    conn.request("POST", "/watch", body=json.dumps(body).encode(), headers=headers)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def test_listen_returns_wav_of_the_window(watch_server):
    ring, port = watch_server
    clock = Clock(3000.0)
    w = mic_ring.RingWriter(str(ring), clock=clock)
    _feed(w, clock, _tone(0, 5) + _tone(7000, 5))
    w.flush()
    status, data = _post(port, {"cmd": "listen", "start": 3005.0, "end": 3010.0})
    assert status == 200 and data["ok"] is True
    with wave.open(io.BytesIO(base64.b64decode(data["wav_b64"]))) as wf:
        assert wf.getframerate() == 16000 and wf.getnchannels() == 1
        frames = wf.readframes(wf.getnframes())
    assert len(frames) == 5 * BPS
    assert set(struct.unpack(f"<{len(frames) // 2}h", frames)) == {7000}


def test_listen_says_why_when_nothing_is_buffered(watch_server):
    _ring, port = watch_server
    status, data = _post(port, {"cmd": "listen", "seconds": 20})
    assert status == 200
    assert data["ok"] is False and "wav_b64" not in data
    assert "wake listener" in data["message"]


def test_listen_refused_through_the_public_proxy(watch_server):
    ring, port = watch_server
    clock = Clock(4000.0)
    w = mic_ring.RingWriter(str(ring), clock=clock)
    _feed(w, clock, _tone(5, 5))
    w.flush()
    status, data = _post(port, {"cmd": "listen", "start": 4000, "end": 4005},
                         {"X-Forwarded-For": "203.0.113.9"})
    assert status == 403
    assert "wav_b64" not in data
