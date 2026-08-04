"""Few-shot wake-word personalization (docs/research-directions.md #3).

oww_score_pcm and enroll_suggest_threshold are the pure logic behind
zeev/wake_enroll.py, the CLI that records a handful of enrollment clips on
the Pi and suggests a per-stem OWW_THRESHOLDS value. Both are testable here
without hardware or the real openwakeword package -- a fake model with a
scripted predict() sequence stands in for it.
"""
import struct
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def wake_enroll():
    """wake_enroll.py lives in zeev/ and imports `from zeev import (...)` at
    module level -- same sys.path trick the `zeev` fixture in conftest.py
    uses, so it resolves the sibling zeev.py rather than needing a package."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "zeev"))
    import wake_enroll as _we
    return _we


# ---------------------------------------------------------------------------
# oww_score_pcm
# ---------------------------------------------------------------------------

class _FakeModel:
    """Scripted stand-in for openwakeword.model.Model: predict() returns the
    next dict off a queue, one per frame; reset() records that it was called
    and clears the queue position back to the start."""

    def __init__(self, script):
        self.script = script
        self.i = 0
        self.reset_calls = 0

    def reset(self):
        self.reset_calls += 1
        self.i = 0

    def predict(self, frame):
        d = self.script[self.i] if self.i < len(self.script) else {}
        self.i += 1
        return d


def _pcm(n_frames, frame_samples=1280):
    """n_frames worth of arbitrary int16 PCM (content doesn't matter -- the
    fake model ignores it and just advances its script)."""
    return struct.pack(f"<{n_frames * frame_samples}h", *([0] * (n_frames * frame_samples)))


def test_oww_score_pcm_takes_the_max_per_stem(zeev):
    model = _FakeModel([
        {"hey_zeev": 0.10, "hey_sarina": 0.05},
        {"hey_zeev": 0.72, "hey_sarina": 0.61},
        {"hey_zeev": 0.30, "hey_sarina": 0.65},
    ])
    scores = zeev.oww_score_pcm(model, _pcm(3))
    assert scores == {"hey_zeev": pytest.approx(0.72), "hey_sarina": pytest.approx(0.65)}


def test_oww_score_pcm_resets_before_scoring(zeev):
    """A previous clip's rolling buffer must never bleed into this one."""
    model = _FakeModel([{"hey_zeev": 0.5}])
    zeev.oww_score_pcm(model, _pcm(1))
    assert model.reset_calls == 1


def test_oww_score_pcm_empty_pcm_returns_empty(zeev):
    model = _FakeModel([{"hey_zeev": 0.9}])
    assert zeev.oww_score_pcm(model, b"") == {}


def test_oww_score_pcm_ignores_a_short_trailing_partial_frame(zeev):
    model = _FakeModel([{"hey_zeev": 0.4}])
    pcm = _pcm(1) + b"\x00" * 100   # trailing partial frame, not a full 2560 bytes
    scores = zeev.oww_score_pcm(model, pcm)
    assert scores == {"hey_zeev": pytest.approx(0.4)}
    assert model.i == 1   # the partial frame was never fed


# ---------------------------------------------------------------------------
# enroll_suggest_threshold
# ---------------------------------------------------------------------------

def test_suggests_a_margin_below_the_weakest_clip(zeev):
    suggested, warning = zeev.enroll_suggest_threshold(
        "hey_zeev", [0.9, 0.85, 0.95], current=0.9)
    assert warning is None
    assert suggested == pytest.approx(0.85 * zeev.OWW_ENROLL_MARGIN, abs=1e-2)


def test_never_raises_above_the_current_threshold(zeev):
    """Enrollment proves genuine wakes score at least this high -- it says
    nothing about false positives, so it must never push the bar up."""
    suggested, warning = zeev.enroll_suggest_threshold(
        "hey_zeev", [0.99, 0.98, 0.97], current=0.5)
    assert warning is None
    assert suggested <= 0.5


def test_defaults_ceiling_to_oww_threshold_when_current_omitted(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "OWW_THRESHOLD", 0.5)
    monkeypatch.setattr(zeev, "OWW_THRESHOLDS", {})
    suggested, warning = zeev.enroll_suggest_threshold("hey_zeev", [0.99, 0.99])
    assert warning is None
    assert suggested <= 0.5


def test_result_never_drops_below_the_floor_when_ceiling_allows_it(zeev):
    # weakest*margin = 0.18*0.85 = 0.153, below OWW_ENROLL_FLOOR (0.2)
    suggested, warning = zeev.enroll_suggest_threshold("hey_zeev", [0.18, 0.5], current=0.9)
    assert warning is None
    assert suggested == pytest.approx(zeev.OWW_ENROLL_FLOOR, abs=1e-2)


def test_warns_instead_of_suggesting_when_too_weak_to_help(zeev):
    """Below OWW_ENROLL_MIN_VIABLE, no threshold is safe -- retraining is the
    real fix, so this must not hand back a number at all."""
    suggested, warning = zeev.enroll_suggest_threshold("hey_zeev", [0.4, 0.05], current=0.9)
    assert suggested is None
    assert "retrain" in warning.lower()


def test_no_clips_is_a_warning_not_a_crash(zeev):
    suggested, warning = zeev.enroll_suggest_threshold("hey_zeev", [], current=0.9)
    assert suggested is None
    assert warning == "no scored clips"


def test_weakest_clip_is_the_binding_constraint(zeev):
    """One quiet clip among several loud ones must still drag the suggestion
    down -- averaging would let it slip below the real minimum."""
    lo, _ = zeev.enroll_suggest_threshold("hey_zeev", [0.95, 0.95, 0.40], current=0.9)
    hi, _ = zeev.enroll_suggest_threshold("hey_zeev", [0.95, 0.95, 0.95], current=0.9)
    assert lo < hi


# ---------------------------------------------------------------------------
# wake_enroll.py CLI: apply_suggestions() .env merge
# ---------------------------------------------------------------------------

def _make_env(tmp_path, lines):
    p = tmp_path / ".env"
    p.write_text("\n".join(lines) + "\n")
    return p


def test_apply_replaces_existing_oww_thresholds_line(wake_enroll, tmp_path, monkeypatch):
    env = _make_env(tmp_path, ["GROQ_API_KEY=x", "OWW_THRESHOLDS=hey_zeev:0.5", "OTHER=y"])
    monkeypatch.setattr(wake_enroll, "_ENV_FILE", env)
    monkeypatch.setattr(wake_enroll, "OWW_THRESHOLDS", {"hey_zeev": 0.5})

    wake_enroll.apply_suggestions({"hey_zeev": 0.35})

    out = env.read_text()
    assert "OWW_THRESHOLDS=hey_zeev:0.35" in out
    assert "GROQ_API_KEY=x" in out and "OTHER=y" in out
    assert out.count("OWW_THRESHOLDS=") == 1


def test_apply_appends_when_no_existing_line(wake_enroll, tmp_path, monkeypatch):
    env = _make_env(tmp_path, ["GROQ_API_KEY=x"])
    monkeypatch.setattr(wake_enroll, "_ENV_FILE", env)
    monkeypatch.setattr(wake_enroll, "OWW_THRESHOLDS", {})

    wake_enroll.apply_suggestions({"hey_sarina": 0.4})

    out = env.read_text()
    assert "OWW_THRESHOLDS=hey_sarina:0.4" in out
    assert "GROQ_API_KEY=x" in out


def test_apply_merges_and_keeps_untouched_stems(wake_enroll, tmp_path, monkeypatch):
    """A suggestion for one stem must not drop another stem's existing
    override -- OWW_THRESHOLDS is a single comma-separated line."""
    env = _make_env(tmp_path, ["OWW_THRESHOLDS=hey_zeev:0.5,hey_sarina:0.8"])
    monkeypatch.setattr(wake_enroll, "_ENV_FILE", env)
    monkeypatch.setattr(wake_enroll, "OWW_THRESHOLDS", {"hey_zeev": 0.5, "hey_sarina": 0.8})

    wake_enroll.apply_suggestions({"hey_zeev": 0.3})

    out = env.read_text()
    line = [l for l in out.splitlines() if l.startswith("OWW_THRESHOLDS=")][0]
    pairs = dict(p.split(":") for p in line.split("=", 1)[1].split(","))
    assert pairs == {"hey_zeev": "0.3", "hey_sarina": "0.8"}


def test_apply_with_no_suggestions_does_not_touch_the_file(wake_enroll, tmp_path, monkeypatch):
    env = _make_env(tmp_path, ["OWW_THRESHOLDS=hey_zeev:0.5"])
    monkeypatch.setattr(wake_enroll, "_ENV_FILE", env)
    before = env.read_text()

    wake_enroll.apply_suggestions({})

    assert env.read_text() == before
