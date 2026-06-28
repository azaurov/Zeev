#!/usr/bin/env python3
"""
weekly_reflection.py — Weekly synthesis of Zeev's conversations with Alex.

Reads the last 7 days of messages from zeev.db, runs an LLM pass to synthesize
themes, patterns, open questions, and emotional context, then stores the result
in the `reflections` table. zeev.py loads the latest reflection at startup and
injects it into every system prompt under "## Weekly reflection:".

Usage:
    python3 zeev/weekly_reflection.py [--days N]   # default 7
    python3 zeev/weekly_reflection.py --show        # print latest stored reflection
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Load .env
_ENV_FILE = BASE_DIR.parent / ".env"
if _ENV_FILE.exists():
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")
BOSGAME_URL   = os.environ.get("BOSGAME_URL", "")
BOSGAME_KEY   = os.environ.get("BOSGAME_KEY", "")
BOSGAME_MODEL = os.environ.get("BOSGAME_MODEL", "llama3.2:1b")

if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY not set", file=sys.stderr)
    sys.exit(1)

import requests

GROQ_URL    = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL  = "llama-3.3-70b-versatile"
MIN_MSGS    = 10   # skip if fewer than this many messages in the window
MAX_MSGS    = 300  # cap to avoid token overflow
MSG_PREVIEW = 500  # chars per message in the transcript

REFLECTION_PROMPT = """\
You are Zeev, Alex's AI companion on a Raspberry Pi. Below is a transcript of \
your conversations with Alex over the past {days} days, grouped by date.

Synthesize what you observed into concise internal notes you'll use in future \
conversations. Focus on:
- Recurring themes or topics Alex returned to more than once
- Alex's emotional state, energy, or mood patterns across the week
- Open questions or unresolved threads that may come up again
- Any shifts in thinking, new interests, or decisions emerging
- Practical context (projects, plans, commitments) relevant going forward

Write as Zeev's first-person internal notes ("I noticed...", "Alex seemed..."). \
Be specific and concrete — reference actual topics discussed, not generic \
observations. Under 250 words. Flowing prose, no bullet points.

TRANSCRIPT:
{transcript}"""


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _open_db():
    db_path = BASE_DIR / "data" / "zeev.db"
    con = sqlite3.connect(str(db_path), check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS reflections (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            period_start TEXT    NOT NULL,
            period_end   TEXT    NOT NULL,
            content      TEXT    NOT NULL,
            ts           TEXT    NOT NULL
        );
    """)
    con.commit()
    return con


def _fetch_messages(con, days):
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = con.execute(
        "SELECT role, content, ts FROM messages WHERE ts >= ? ORDER BY id",
        (cutoff,),
    ).fetchall()
    return rows


def _build_transcript(rows):
    """Group messages by date and format as a readable transcript."""
    by_day = {}
    for r in rows:
        try:
            day = r["ts"][:10]
        except Exception:
            day = "unknown"
        by_day.setdefault(day, []).append(r)

    lines = []
    for day in sorted(by_day):
        lines.append(f"\n[{day}]")
        for r in by_day[day]:
            role = "Alex" if r["role"] == "user" else "Zeev"
            content = r["content"][:MSG_PREVIEW]
            if len(r["content"]) > MSG_PREVIEW:
                content += "…"
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _save_reflection(con, period_start, period_end, content):
    con.execute(
        "INSERT INTO reflections (period_start, period_end, content, ts) VALUES (?, ?, ?, ?)",
        (period_start, period_end, content, datetime.now().isoformat()),
    )
    con.commit()


def _count_reflections(con):
    return con.execute("SELECT COUNT(*) FROM reflections").fetchone()[0]


# ---------------------------------------------------------------------------
# LLM — bosgame first (free), Groq 70B fallback
# ---------------------------------------------------------------------------

def _call_bosgame(prompt):
    if not BOSGAME_URL or not BOSGAME_KEY:
        return None, "bosgame not configured"
    try:
        r = requests.post(
            f"{BOSGAME_URL}/v1/chat/completions",
            headers={"X-Zeev-Key": BOSGAME_KEY, "Content-Type": "application/json"},
            json={
                "model": "llama3.1:8b",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400,
                "temperature": 0.7,
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip(), None
    except Exception as e:
        return None, str(e)


def _call_groq(prompt):
    try:
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400,
                "temperature": 0.7,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip(), None
    except Exception as e:
        return None, str(e)


def _synthesize(transcript, days):
    prompt = REFLECTION_PROMPT.format(days=days, transcript=transcript)
    content, err = _call_bosgame(prompt)
    if content:
        print("  [LLM: bosgame]")
        return content, None
    print(f"  [bosgame failed: {err}] — trying Groq…")
    content, err = _call_groq(prompt)
    if content:
        print("  [LLM: Groq 70B]")
        return content, None
    return None, err


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(days=7, verbose=True):
    con = _open_db()
    rows = _fetch_messages(con, days)

    if len(rows) < MIN_MSGS:
        print(f"Only {len(rows)} messages in the last {days} days (min {MIN_MSGS}) — skipping.")
        con.close()
        return False

    # Cap to avoid token overflow (take most recent MAX_MSGS)
    if len(rows) > MAX_MSGS:
        rows = rows[-MAX_MSGS:]
        if verbose:
            print(f"  (capped to last {MAX_MSGS} messages)")

    period_start = rows[0]["ts"][:10]
    period_end   = rows[-1]["ts"][:10]

    if verbose:
        msg_count = len(rows)
        print(f"Reflecting on {msg_count} messages ({period_start} → {period_end})…")

    transcript = _build_transcript(rows)

    if verbose:
        print("  Running LLM synthesis…", flush=True)

    content, err = _synthesize(transcript, days)
    if err:
        print(f"  ERROR: {err}")
        con.close()
        return False

    _save_reflection(con, period_start, period_end, content)
    total = _count_reflections(con)

    if verbose:
        print(f"\n{'='*50}")
        print(f"Reflection stored (total: {total})")
        print(f"\n{content}\n")

    con.close()
    return True


def show_latest(days=None):
    con = _open_db()
    row = con.execute(
        "SELECT period_start, period_end, content, ts FROM reflections ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    if not row:
        print("No reflections stored yet.")
        return
    print(f"Latest reflection ({row['period_start']} → {row['period_end']}, stored {row['ts'][:16]}):")
    print(f"\n{row['content']}\n")


def main():
    parser = argparse.ArgumentParser(description="Zeev weekly reflection")
    parser.add_argument("--days", type=int, default=7, help="Days of history to synthesize (default 7)")
    parser.add_argument("--show", action="store_true", help="Print the latest stored reflection and exit")
    args = parser.parse_args()

    if args.show:
        show_latest()
        return

    print(f"Weekly reflection — last {args.days} days")
    success = run(days=args.days)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
