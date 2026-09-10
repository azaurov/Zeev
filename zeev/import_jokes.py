#!/usr/bin/env python3
"""Import jokes from an input file into data/adult_jokes*.json.

Reads jokes the user has already written or sourced -- this script does not
generate joke content itself. Two input formats are accepted:

  1. JSON: a list of {"setup": ..., "punchline": ...} objects.
  2. Plain text: jokes separated by a line containing only "---". Within a
     joke, the first line is the setup and the remaining lines are the
     punchline (blank lines inside a punchline are preserved, since several
     existing pool entries use them between beats of a joke). A joke with
     only one line gets an empty punchline (matches existing one-liners).

Dedupes against the existing pool (normalized exact match on setup+punchline)
and against duplicates within the input itself. Backs up the target file to
<name>.json.bak.<timestamp> before writing, per this project's existing
convention (see CLAUDE.md "Adult jokes" pool-cleanup notes).

Usage:
    python3 zeev/import_jokes.py new_jokes.txt
    python3 zeev/import_jokes.py new_jokes.json --lang es
    python3 zeev/import_jokes.py new_jokes.txt --dry-run
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

LANG_FILES = {
    "en": "adult_jokes.json",
    "es": "adult_jokes_es.json",
    "ru": "adult_jokes_ru.json",
    "he": "adult_jokes_he.json",
    "zh": "adult_jokes_zh.json",
}

_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def _key(j: dict) -> str:
    return _norm(j.get("setup", "")) + "\x00" + _norm(j.get("punchline", ""))


def parse_plain_text(text: str) -> list[dict]:
    blocks = re.split(r"\n[ \t]*---[ \t]*\n", text.strip())
    jokes = []
    for block in blocks:
        lines = block.strip("\n").splitlines()
        # drop leading/trailing blank lines but keep interior ones
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            continue
        setup = lines[0].strip()
        punchline = "\n".join(lines[1:]).strip()
        jokes.append({"setup": setup, "punchline": punchline})
    return jokes


def parse_input(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON input must be a list of {setup, punchline} objects")
        jokes = []
        for entry in data:
            if not isinstance(entry, dict) or "setup" not in entry:
                raise ValueError(f"skipping malformed entry: {entry!r}")
            jokes.append({"setup": entry.get("setup", ""), "punchline": entry.get("punchline", "")})
        return jokes
    return parse_plain_text(text)


def main():
    parser = argparse.ArgumentParser(description="Import jokes from a file into the adult joke pool")
    parser.add_argument("input", type=Path, help="input file: .json (list of {setup,punchline}) or plain text (blank-line separated)")
    parser.add_argument("--lang", default="en", choices=sorted(LANG_FILES), help="target pool (default: en)")
    parser.add_argument("--dry-run", action="store_true", help="parse and report, don't write")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    try:
        new_jokes = parse_input(args.input)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if not new_jokes:
        print("No jokes found in input file.")
        sys.exit(0)

    target_path = DATA_DIR / LANG_FILES[args.lang]
    existing = []
    if target_path.exists():
        existing = json.loads(target_path.read_text(encoding="utf-8"))

    seen = {_key(j) for j in existing}
    added = []
    skipped_dupe = 0
    skipped_empty = 0
    for j in new_jokes:
        if not j.get("setup", "").strip():
            skipped_empty += 1
            continue
        k = _key(j)
        if k in seen:
            skipped_dupe += 1
            continue
        seen.add(k)
        added.append(j)

    print(f"Parsed {len(new_jokes)} joke(s) from {args.input}")
    print(f"  {len(added)} new, {skipped_dupe} duplicate(s) skipped, {skipped_empty} empty-setup skipped")

    if args.dry_run:
        for j in added[:10]:
            print(f"  + {j['setup'][:70]!r}")
        if len(added) > 10:
            print(f"  ... and {len(added) - 10} more")
        print("Dry run -- nothing written.")
        return

    if not added:
        print("Nothing to add.")
        return

    if target_path.exists():
        backup_path = target_path.with_suffix(f".json.bak.{int(time.time())}")
        backup_path.write_text(target_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Backed up existing pool to {backup_path}")

    combined = existing + added
    target_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(combined)} total joke(s) to {target_path} ({len(added)} added)")


if __name__ == "__main__":
    main()
