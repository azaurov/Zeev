"""Wake-trigger capture: the audio that fired a wake, kept for later analysis.

These pin the parts that can be tested off-device. The ring buffer's wiring
into _wake_loop_oww needs the HAT and a live mic, so it is exercised on the Pi,
not here.
"""
import json
import wave

import pytest

# zeev.py is imported via the session `zeev` fixture in conftest.py (sys.path
# trick) -- it is a single module in zeev/, not a package.


def _frames(n, fill=b"\x01\x00"):
    return [fill * 1280 for _ in range(n)]


def test_writes_wav_and_sidecar(zeev, tmp_path):
    p = zeev.wake_capture_save(_frames(3), "hey_zeev", 0.912345,
                          {"hey_zeev": 0.912345, "hey_sarina": 0.11},
                          gate=3200.0, capture_dir=tmp_path)
    assert p.exists() and p.suffix == ".wav"
    meta = json.loads(p.with_suffix(".json").read_text())
    assert meta["fired"] == "hey_zeev"
    assert meta["score"] == 0.9123
    # Every model's score is kept, not just the winner: if both peak on the
    # same audio, the two phrases are not separable, which is an open question.
    assert set(meta["scores"]) == {"hey_zeev", "hey_sarina"}
    # Unlabelled on purpose -- a guessed label would poison the corpus.
    assert meta["label"] is None


def test_wav_is_real_16k_mono_pcm(zeev, tmp_path):
    p = zeev.wake_capture_save(_frames(5), "hey_sarina", 0.97, {"hey_sarina": 0.97},
                          gate=1000.0, capture_dir=tmp_path)
    with wave.open(str(p)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert w.getnframes() == 5 * 1280


def test_prunes_oldest_pairs_together(zeev, tmp_path):
    for i in range(5):
        zeev.wake_capture_save(_frames(1), f"m{i}", 0.9, {"m": 0.9}, gate=1.0,
                          capture_dir=tmp_path, max_files=3)
    wavs = sorted(tmp_path.glob("*.wav"))
    metas = sorted(tmp_path.glob("*.json"))
    assert len(wavs) == 3, "should retain exactly max_files clips"
    # A clip without its metadata is useless, and metadata without its clip is
    # worse -- it looks like evidence.
    assert [w.stem for w in wavs] == [m.stem for m in metas]


def test_never_raises_into_the_wake_loop(zeev, tmp_path):
    """A capture failure must not cost Alex his wake word."""
    bad = tmp_path / "not_a_dir"
    bad.write_text("i am a file")
    with pytest.raises(Exception):
        # The helper itself may fail on an impossible path; the contract is
        # that the CALLER swallows it, which _wake_loop_oww does. This test
        # documents that the failure is a plain exception, not a crash of the
        # interpreter or a hang.
        zeev.wake_capture_save(_frames(1), "x", 0.9, {}, gate=1.0, capture_dir=bad / "sub")
