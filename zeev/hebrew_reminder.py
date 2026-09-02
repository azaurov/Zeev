#!/usr/bin/env python3
"""
hebrew_reminder.py — Recurring daily reminder to study for Arthur's Hebrew
class.

Fires via systemd timer at 5pm local. Inserts a fresh row into the existing
`reminders` table each time, so the existing reminders/_reminder_loop()
mechanism announces it once the device is idle -- no LLM synthesis, no
calendar/facts, just a recurring trigger for the existing one-shot reminder
pipeline (the table itself has no notion of a recurring reminder, so this
script IS the recurrence: a fresh due-now row every time it's scheduled).

Usage:
    python3 zeev/hebrew_reminder.py
"""

import os
import sys
import time
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

import zeev as z

REMINDER_TEXT = "Time to study for Arthur's Hebrew class."


def main():
    now = time.time()
    with z._db_lock:
        con = z._db()
        con.execute(
            "INSERT INTO reminders (text, due_ts, created_ts, fired) VALUES (?, ?, ?, 0)",
            (REMINDER_TEXT, now, now),
        )
        con.commit()
    print(f"Inserted reminder: {REMINDER_TEXT}")


if __name__ == "__main__":
    main()
