#!/usr/bin/env python3
"""
wake_enroll.py — few-shot wake-word personalization (docs/research-directions.md #3)

Records a handful of enrollment clips of the household voice actually saying
the configured wake phrase(s), scores them against the loaded openWakeWord
model(s), and suggests a per-stem OWW_THRESHOLDS value personalized to this
voice + mic + room -- without the ~90-minute Colab retrain in
docs/wake-word-training.md.

It can only ever LOWER a threshold, never raise one: a few positive clips
prove genuine wakes score at least this high, but say nothing about false
positives on other audio (that needs negative examples -- direction #4,
active learning from false-positive logs -- not this).

This is Pi-only in practice: it needs a real mic (arecord) and the
openwakeword package. Run it on the device, not in dev.

Usage:
    python3 zeev/wake_enroll.py                  # 5 clips, print suggestions
    python3 zeev/wake_enroll.py --clips 8
    python3 zeev/wake_enroll.py --apply           # also write OWW_THRESHOLDS to .env
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

_ENV_FILE = BASE_DIR.parent / ".env"
if _ENV_FILE.exists():
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

# Reuse the shared scoring/suggestion logic and config rather than
# duplicating it -- see zeev.py's oww_score_pcm / enroll_suggest_threshold,
# right after oww_best. Importing the whole module is fine: it has no
# import-time side effects beyond reading env vars and defining functions
# (same assumption tests/conftest.py's `zeev` fixture relies on).
from zeev import (
    OWW_MODEL_PATH, OWW_THRESHOLDS, OWW_THRESHOLD,
    oww_score_pcm, enroll_suggest_threshold,
)

SAMPLE_RATE = 16000
MIC_DEVICE = os.environ.get("MIC_DEVICE", "plughw:wm8960soundcard,0")


def record_clip(seconds, mic_device):
    """Return raw 16kHz mono S16LE PCM bytes, or None on failure."""
    try:
        proc = subprocess.run(
            ["arecord", "-D", mic_device, "-f", "S16_LE", "-r", str(SAMPLE_RATE),
             "-c", "1", "-t", "raw", "-d", str(seconds), "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=seconds + 10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  recording failed: {e}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"  arecord exited {proc.returncode}: {proc.stderr.decode(errors='replace')[:200]}",
              file=sys.stderr)
        return None
    return proc.stdout


def load_model(paths):
    try:
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        from openwakeword.model import Model
    except ImportError:
        print("openwakeword is not installed here. This tool is meant to run "
              "on the Pi (see CLAUDE.md's openWakeWord section) -- pip install "
              "onnxruntime openwakeword there, not in dev.", file=sys.stderr)
        sys.exit(1)
    missing = [p for p in paths if not Path(p).exists()]
    for p in missing:
        print(f"model not found: {p}", file=sys.stderr)
    paths = [p for p in paths if p not in missing]
    if not paths:
        print("no valid model paths in OWW_MODEL_PATH", file=sys.stderr)
        sys.exit(1)
    return Model(wakeword_model_paths=paths), paths


def run_enrollment(n_clips, duration, mic_device):
    paths = [p.strip() for p in OWW_MODEL_PATH.split(",") if p.strip()]
    if not paths:
        print("OWW_MODEL_PATH is not set -- nothing to enroll against.", file=sys.stderr)
        sys.exit(1)
    model, paths = load_model(paths)
    stems = [Path(p).stem for p in paths]
    print(f"Loaded: {', '.join(stems)}\n")

    # One list of max-scores per stem, one entry per clip.
    clip_scores = {stem: [] for stem in stems}
    for i in range(1, n_clips + 1):
        input(f"Clip {i}/{n_clips}: press Enter, then say the wake phrase "
              f"({duration:.1f}s recording starts immediately) ")
        pcm = record_clip(duration, mic_device)
        if pcm is None:
            print("  skipped (recording failed)\n")
            continue
        scores = oww_score_pcm(model, pcm)
        for stem in stems:
            score = scores.get(stem, 0.0)
            clip_scores[stem].append(score)
        line = "  " + "  ".join(f"{stem}={scores.get(stem, 0.0):.2f}" for stem in stems)
        print(line + "\n")

    print("=" * 60)
    print("Suggested OWW_THRESHOLDS")
    print("=" * 60)
    suggestions = {}
    for stem in stems:
        scores = [s for s in clip_scores[stem] if s is not None]
        current = OWW_THRESHOLDS.get(stem, OWW_THRESHOLD)
        suggested, warning = enroll_suggest_threshold(stem, scores, current=current)
        clip_str = ", ".join(f"{s:.2f}" for s in scores) or "(no clips scored)"
        print(f"\n{stem}: clips [{clip_str}]  current={current:.2f}")
        if warning:
            print(f"  -> {warning}")
        else:
            print(f"  -> suggested {suggested:.2f}"
                  + (" (no change)" if suggested >= current else f" (down from {current:.2f})"))
            if suggested < current:
                suggestions[stem] = suggested
    return suggestions


def apply_suggestions(suggestions):
    if not suggestions:
        print("\nNothing to apply.")
        return
    if not _ENV_FILE.exists():
        print(f"\n{_ENV_FILE} not found -- cannot apply automatically. Add this "
              "line to your .env by hand instead:")
        print("OWW_THRESHOLDS=" + ",".join(f"{k}:{v}" for k, v in suggestions.items()))
        return

    merged = dict(OWW_THRESHOLDS)
    merged.update(suggestions)
    new_line = "OWW_THRESHOLDS=" + ",".join(f"{k}:{v}" for k, v in merged.items())

    lines = _ENV_FILE.read_text().splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith("OWW_THRESHOLDS="):
            print(f"\n- {line}")
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)
    print(f"+ {new_line}")
    _ENV_FILE.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {_ENV_FILE}. Restart zeev-device for it to take effect:\n"
          "  ssh ragnar@ragnarok \"sudo systemctl restart zeev-device\"")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips", type=int, default=5, help="enrollment clips to record (default 5)")
    ap.add_argument("--duration", type=float, default=2.5, help="seconds per clip (default 2.5)")
    ap.add_argument("--mic", default=MIC_DEVICE, help=f"ALSA device (default {MIC_DEVICE})")
    ap.add_argument("--apply", action="store_true",
                     help="write suggested thresholds into .env (default: print only)")
    args = ap.parse_args()

    suggestions = run_enrollment(args.clips, args.duration, args.mic)
    if args.apply:
        apply_suggestions(suggestions)
    else:
        print("\n(dry run -- pass --apply to write these into .env)")


if __name__ == "__main__":
    main()
