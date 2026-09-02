#!/usr/bin/env python3
"""
daily_suggestions.py — Daily personalized suggestions for Alex.

Fires via systemd timer at 9am local. Fetches today's Google Calendar events
and USER_FACTS, synthesizes a short spoken-style morning message, stores it
in the `daily_suggestions` table (zeev.py loads the latest same-day row and
injects it into every system prompt under "## Today's suggestions:", same
shape as weekly_reflection.py's block but scoped to today only), and inserts
an immediate reminder row so the existing reminders/_reminder_loop()
mechanism speaks it on the device the moment it's idle.

Unlike quantum_daily.py/weekly_reflection.py/news_digest.py, this imports
zeev.py directly (`import zeev as z`) rather than duplicating logic --
gcal_fetch() carries real OAuth-token-refresh complexity that would be risky
to reimplement, and the run-zeev skill already established that importing
zeev.py as a module is safe (all side effects gated behind __main__).

Usage:
    python3 zeev/daily_suggestions.py
"""

import os
import sys
import time
from datetime import datetime
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

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY not set", file=sys.stderr)
    sys.exit(1)

import zeev as z

SUGGESTIONS_PROMPT = """\
You are Zeev, Alex's AI companion. Based on what's below, write a short, \
warm, spoken-style morning message for Alex -- 2-4 sentences, natural to \
say aloud, not a list. Mention anything genuinely relevant from today's \
calendar or what you know about him; if nothing stands out, a brief \
general well-wishing is fine. Do not invent events or facts not given below.

TODAY'S CALENDAR:
{calendar}

WHAT I KNOW ABOUT ALEX:
{facts}
"""


def _gather_context():
    try:
        calendar = z.gcal_fetch(days=1) or "(no calendar access or no events today)"
    except Exception as e:
        print(f"  [gcal_fetch failed: {e}]")
        calendar = "(no calendar access or no events today)"
    # load_memory() already acquires _db_lock internally -- do not wrap this
    # call in the lock too, threading.Lock is not reentrant and it would
    # deadlock the process on its own first line.
    facts = z.load_memory()
    facts_str = "\n".join(f"- {f}" for f in facts[-20:]) if facts else "(nothing stored yet)"
    return calendar, facts_str


def synthesize():
    calendar, facts_str = _gather_context()
    prompt = SUGGESTIONS_PROMPT.format(calendar=calendar, facts=facts_str)
    msgs = [{"role": "user", "content": prompt}]
    # Same feiergente -> bosgame -> Groq cascade weekly_reflection.py uses,
    # sharing its busy-lock reasoning (see zeev.py's _feiergente_complete
    # docstring) -- this is background-only, never a live turn.
    text, err = z._feiergente_complete(msgs, max_tokens=250)
    if text:
        print("  [LLM: feiergente]")
        return text, None
    text, err = z._bosgame_complete(msgs, max_tokens=250)
    if text:
        print("  [LLM: bosgame]")
        return text, None
    print(f"  [bosgame failed: {err}] — trying Groq…")
    # gpt-oss-20b draws hidden reasoning from the same max_tokens budget --
    # reasoning_effort="low" is the established fix (see CLAUDE.md's World
    # news / _quantum_llm docstring for the same pattern).
    text, err = z._llm_complete(msgs, z.MODELS["1"][0], max_tokens=250, reasoning_effort="low")
    if text:
        text = z._strip_think_text(text)
    if not text:
        return None, err or "Groq reply was empty after stripping <think> reasoning (likely truncated)"
    print("  [LLM: Groq]")
    return text, None


def main():
    content, err = synthesize()
    if not content:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    today = datetime.now().strftime("%Y-%m-%d")
    now = time.time()
    with z._db_lock:
        con = z._db()
        con.execute(
            "INSERT INTO daily_suggestions (date, content, ts) VALUES (?, ?, ?)",
            (today, content, now),
        )
        con.execute(
            "INSERT INTO reminders (text, due_ts, created_ts, fired) VALUES (?, ?, ?, 0)",
            (content, now, now),
        )
        con.commit()
    print(f"Stored daily suggestion for {today}: {content[:100]}")


if __name__ == "__main__":
    main()
