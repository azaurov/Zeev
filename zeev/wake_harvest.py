#!/usr/bin/env python3
"""
wake_harvest.py — active learning from wake-word false-positive logs
(docs/research-directions.md #4)

Parses journalctl for openWakeWord triggers and the transcript each one
produced, walks a reviewer through the ones that don't already have a saved
verdict, and saves the ones marked false as hard negatives -- a JSON array
of strings droppable straight into the `custom_negative_strings` list of the
training notebook (docs/wake-word-training.md).

Usage:
    python3 zeev/wake_harvest.py                      # last 7 days
    python3 zeev/wake_harvest.py --since "24 hours ago"
    python3 zeev/wake_harvest.py --out data/wake_negatives.json
"""

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# A trigger and its transcript are logged by the SAME thread with nothing of
# its own printed in between -- _wake_dispatch has no prints on the normal
# path, see CLAUDE.md's openWakeWord section. Other threads (reminders,
# gcal, memory maintenance) can interleave a few lines into the journal
# though, so this is generous, not tight. Past it, the trigger is treated as
# never resolved rather than paired with an unrelated later turn.
_MAX_LOOKAHEAD = 30

_TRIGGER_RE = re.compile(r"\[wake\]\s+(.*?)\s+trigger\s+\(score\s+([0-9.]+)\)")
_NOISE_RE   = re.compile(r"\[wake\] discarded noise transcript:\s*(.*)$")
_YOU_RE     = re.compile(r"\bYou:\s*(?:\[voice\]\s*)?(.+)$")
_ANSI_RE    = re.compile(r"\x1b\[[0-9;]*m")


def _unrepr(text):
    """`discarded noise transcript` logs its text via Python's !r (repr), so
    a transcript containing an apostrophe or a backslash breaks a plain
    quote-strip -- repr switches quote style or escapes rather than leaving
    the text untouched. ast.literal_eval parses it properly; if the log
    format ever changes and this isn't actually a repr, fall back to the raw
    text rather than raising.
    """
    try:
        return ast.literal_eval(text.strip())
    except (ValueError, SyntaxError):
        return text.strip()


def parse_wake_triggers(log_lines):
    """Pure parser: journalctl lines -> ([{"model", "score", "transcript"}], dropped).

    A trigger with no transcript ever following it (the turn crashed, the
    mic never opened, or the log window cuts off mid-turn) has nothing to
    harvest and is dropped -- but counted, via `dropped`, so the caller can
    report it instead of the trigger count silently not adding up.
    """
    triggers = []
    pending = None       # trigger dict awaiting its transcript
    pending_age = 0
    dropped = 0

    for raw_line in log_lines:
        line = _ANSI_RE.sub("", raw_line).strip()

        tm = _TRIGGER_RE.search(line)
        if tm:
            if pending is not None:
                dropped += 1
            pending = {"model": tm.group(1), "score": float(tm.group(2)), "transcript": None}
            pending_age = 0
            continue

        if pending is None:
            continue

        pending_age += 1
        if pending_age > _MAX_LOOKAHEAD:
            dropped += 1
            pending = None
            continue

        nm = _NOISE_RE.search(line)
        if nm:
            pending["transcript"] = _unrepr(nm.group(1))
            triggers.append(pending)
            pending = None
            continue

        ym = _YOU_RE.search(line)
        if ym:
            pending["transcript"] = ym.group(1).strip()
            triggers.append(pending)
            pending = None
            continue

    if pending is not None:
        dropped += 1
    return triggers, dropped


def fetch_journal(unit="zeev-device", since="7 days ago"):
    try:
        result = subprocess.run(
            ["journalctl", "-u", unit, "--no-pager", "--since", since],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"journalctl failed: {e}", file=sys.stderr)
        return []
    return result.stdout.splitlines()


def load_negatives(path):
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError:
        print(f"warning: {path} is not valid JSON, starting fresh", file=sys.stderr)
        return []
    return data if isinstance(data, list) else []


def save_negatives(path, negatives):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(negatives, f, indent=2)


def review_triggers(triggers, existing, ask=input):
    """Interactive loop. Returns the newly-confirmed negatives.

    Skips a transcript already saved from a prior run, or already seen
    earlier in THIS run -- the same false wake often recurs (a TV ad plays
    daily), and re-asking about a duplicate trains the reviewer to
    rubber-stamp 'y' without reading it, which is worse than skipping it.

    `ask` is injectable so this loop is testable without real stdin.
    """
    seen = set(existing)
    new_negatives = []
    for t in triggers:
        text = t["transcript"]
        if not text or text in seen:
            continue
        seen.add(text)
        print(f"\nModel: {t['model']}, Score: {t['score']:.2f}")
        print(f"Transcript: {text!r}")
        ans = ask("Is this a FALSE trigger? (y/n/q to quit): ").strip().lower()
        if ans == "q":
            break
        if ans == "y":
            new_negatives.append(text)
    return new_negatives


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="7 days ago", help="journalctl --since value")
    ap.add_argument("--unit", default="zeev-device", help="systemd unit to read (default zeev-device)")
    ap.add_argument("--out", default=str(BASE_DIR.parent / "data" / "wake_negatives.json"),
                     help="JSON file of hard negatives to append to")
    args = ap.parse_args()

    print(f"Fetching {args.unit} logs since {args.since!r}...")
    log_lines = fetch_journal(args.unit, args.since)
    triggers, dropped = parse_wake_triggers(log_lines)
    print(f"Found {len(triggers)} trigger(s) with a transcript"
          + (f", {dropped} without one (skipped)" if dropped else "") + ".")
    if not triggers:
        return

    out_path = Path(args.out)
    existing = load_negatives(out_path)
    new_negatives = review_triggers(triggers, existing)

    if new_negatives:
        save_negatives(out_path, existing + new_negatives)
        print(f"\nSaved {len(new_negatives)} new hard negative(s) to {args.out}.")
        print("Drop these into `custom_negative_strings` in the openWakeWord "
              "training notebook -- see docs/wake-word-training.md.")
    else:
        print("\nNo new hard negatives harvested.")


if __name__ == "__main__":
    main()
