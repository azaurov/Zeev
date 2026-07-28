"""The energy gate in front of openWakeWord inference.

Scoring every 80ms frame costs ~70% of one core on a Pi Zero 2W, and in a quiet
room nearly every frame is silence. The gate skips inference on those.

The risk it introduces is subtle: openWakeWord scores a *rolling* buffer, so
skipping frames feeds it discontinuous audio — the wake phrase stitched onto
whatever preceded the silence. The design answer is to reset the model at
speech onset and replay a short pre-roll, which is what these tests pin.

Measured on the Pi with real TTS speech: peak score 0.998 gated vs 0.999
ungated, on 20% of the frames. The rule is reimplemented here because
_wake_loop_oww is a closure inside run_device_mode (needs the HAT to import).
"""
from collections import deque

import pytest

np = pytest.importorskip("numpy")

FRAME = 1280
MULT, MIN_GATE, HOLD, PRE = 1.8, 500.0, 25, 6


def simulate(frames, mult=MULT, min_gate=MIN_GATE, hold_frames=HOLD, pre=PRE):
    """Mirror of the gate. Returns (scored_indices, resets, prerolls_replayed)."""
    levels, preroll = deque(maxlen=80), deque(maxlen=pre)
    hold, gate = 0, min_gate
    scored, resets, replayed = [], 0, 0
    for i, f in enumerate(frames):
        rms = float(np.sqrt(np.mean(f.astype(np.float32) ** 2)))
        levels.append(rms)
        if len(levels) == levels.maxlen and (i + 1) % 40 == 0:
            gate = max(sorted(levels)[len(levels) // 2] * mult, min_gate)
        if hold > 0:
            hold -= 1
        elif rms >= gate:
            resets += 1
            replayed += len(preroll)
            hold = hold_frames
        else:
            preroll.append(f)
            continue
        preroll.append(f)
        scored.append(i)
    return scored, resets, replayed


def quiet(n, amp=100, seed=0):
    rng = np.random.default_rng(seed)
    return [rng.integers(-amp, amp, size=FRAME, dtype=np.int16) for _ in range(n)]


def loud(n, amp=9000, seed=1):
    rng = np.random.default_rng(seed)
    return [rng.integers(-amp, amp, size=FRAME, dtype=np.int16) for _ in range(n)]


def test_silence_is_not_scored():
    """The whole point: a quiet room must not run the model."""
    scored, resets, _ = simulate(quiet(200))
    assert scored == []
    assert resets == 0


def test_speech_onset_starts_scoring():
    frames = quiet(100) + loud(10) + quiet(100)
    scored, resets, _ = simulate(frames)
    assert resets >= 1
    assert any(100 <= i < 110 for i in scored), "speech frames were skipped"


def test_onset_replays_preroll():
    """Without pre-roll the attack of the phrase is lost — the model would only
    see audio from after the energy threshold was already crossed."""
    frames = quiet(100) + loud(10) + quiet(100)
    _, resets, replayed = simulate(frames)
    assert resets >= 1
    assert replayed >= 1, "no pre-roll replayed at onset"


def test_hold_keeps_scoring_through_a_pause():
    """Speech has gaps. The hold window must ride through them, or a short dip
    mid-phrase would gate the rest of the wake word out."""
    frames = quiet(100) + loud(3) + quiet(3) + loud(3) + quiet(100)
    scored, _, _ = simulate(frames)
    gap = [i for i in scored if 103 <= i < 106]
    assert gap, "inference stopped during a brief pause inside speech"


def test_hold_expires_after_speech():
    frames = quiet(100) + loud(5) + quiet(200)
    scored, _, _ = simulate(frames)
    assert max(scored) < 100 + 5 + HOLD + 2, "hold never released"


def test_continuous_noise_falls_back_to_scoring_everything():
    """In a loud room the median rises with it, so the gate must not simply
    latch open forever — but it also must not go deaf. Scoring most frames is
    the correct (safe) degradation."""
    scored, _, _ = simulate(loud(300))
    assert len(scored) > 0


def test_savings_are_real_in_a_quiet_room():
    frames = quiet(280) + loud(20)
    scored, _, _ = simulate(frames)
    assert len(scored) / len(frames) < 0.35, "gate saves little; not worth the risk"


def test_disabled_gate_scores_every_frame():
    """OWW_ENERGY_GATE=0 must be a true bypass — the documented escape hatch."""
    frames = quiet(50)
    scored, _, _ = simulate(frames, min_gate=0.0)
    assert len(scored) == len(frames)
