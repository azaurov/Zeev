#!/usr/bin/env python3
"""news_probe.py — faithfulness grading for the world-news shpeel.

Same shape as rag_probe.py's RAG-faithfulness dashboard, applied to the
curated world-news digest: grades the LLM-written summary against the raw
Tavily snippets news_digest.py actually gathered for it, via a separate
fresh-context grader call, and logs the verdict to `news_probes` so drift or
hallucination in the digest can be tracked over time.

Standalone (own .env loading, own Groq/bosgame calls) rather than importing
zeev.py as a module -- unlike rag_probe.py, this doesn't need any of zeev's
retrieval machinery (torah_search, retrieve_semantic, _build_system_prompt),
only the raw digest rows news_digest.py already stored in zeev.db.

Usage:
    python3 zeev/news_probe.py            # grade every ungraded digest
    python3 zeev/news_probe.py --all       # re-grade every digest, including graded ones
    python3 zeev/news_probe.py --report    # rolling faithfulness report
"""

import argparse
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
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

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
BOSGAME_URL  = os.environ.get("BOSGAME_URL", "")
BOSGAME_KEY  = os.environ.get("BOSGAME_KEY", "")

if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY not set", file=sys.stderr)
    sys.exit(1)

import requests

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "qwen/qwen3.6-27b"


# ---------------------------------------------------------------------------
# Grading — fresh context, no chat history
# ---------------------------------------------------------------------------

_GRADE_PROMPT = """Below is raw search-snippet context and a spoken world-news \
summary generated from it.

CONTEXT:
{context}

SUMMARY:
{answer}

Does the summary state anything -- a fact, name, number, or claim -- not \
supported by the context above? Minor paraphrasing and reasonable inference \
from what's in the context is fine; a claim with no basis in the context at \
all is not. Reply GROUNDED, UNGROUNDED, or UNSURE on the first line, then \
one line explaining why."""

_UNGROUNDED_RE = re.compile(r"\bUNGROUNDED\b", re.I)
_GROUNDED_RE   = re.compile(r"\bGROUNDED\b", re.I)
_UNSURE_RE     = re.compile(r"\bUNSURE\b", re.I)


def parse_grade(raw):
    """First line -> (grounded: 1|0|None, note: str).

    UNGROUNDED-first ordering, same reasoning as rag_probe.py's parse_grade:
    "GROUNDED" is a literal substring of "UNGROUNDED", so check order matters.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, "empty grader response"
    first_line, _, rest = raw.partition("\n")
    note = rest.strip()

    for pattern, value in ((_UNGROUNDED_RE, 0), (_UNSURE_RE, None), (_GROUNDED_RE, 1)):
        if pattern.search(first_line):
            return value, note

    for pattern, value in ((_UNGROUNDED_RE, 0), (_UNSURE_RE, None), (_GROUNDED_RE, 1)):
        if pattern.search(raw):
            return value, note or raw[:200]

    return None, f"unparseable grader output: {raw[:200]}"


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
                "max_tokens": 150,
                "temperature": 0.3,
            },
            timeout=60,
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
                "max_tokens": 150,
                "temperature": 0.3,
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip(), None
    except Exception as e:
        return None, str(e)


def _grade(context, answer):
    prompt = _GRADE_PROMPT.format(context=context, answer=answer)
    text, err = _call_bosgame(prompt)
    if not text:
        text, err = _call_groq(prompt)
    if not text:
        return None, f"grader call failed: {err}"
    return parse_grade(text)


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _open_db():
    db_path = BASE_DIR / "data" / "zeev.db"
    con = sqlite3.connect(str(db_path), check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS world_news (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            content  TEXT    NOT NULL,
            ts       REAL    NOT NULL,
            snippets TEXT
        );
        CREATE TABLE IF NOT EXISTS news_probes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            digest_id   INTEGER NOT NULL,
            ts          REAL    NOT NULL,
            grounded    INTEGER,
            grader_note TEXT
        );
    """)
    try:
        con.execute("ALTER TABLE world_news ADD COLUMN snippets TEXT")
    except sqlite3.OperationalError:
        pass  # column already present
    con.commit()
    return con


def _gradable_digests(con, include_graded=False):
    """Rows with snippets stored -- older digests saved before `snippets`
    existed have NULL there and can never be graded, so they're excluded
    rather than sent to the grader with an empty context."""
    if include_graded:
        return con.execute(
            "SELECT id, content, ts, snippets FROM world_news "
            "WHERE snippets IS NOT NULL AND snippets != '' ORDER BY id"
        ).fetchall()
    return con.execute(
        "SELECT id, content, ts, snippets FROM world_news "
        "WHERE snippets IS NOT NULL AND snippets != '' "
        "AND id NOT IN (SELECT digest_id FROM news_probes) ORDER BY id"
    ).fetchall()


def _save(con, digest_id, grounded, note):
    con.execute(
        "INSERT INTO news_probes (digest_id, ts, grounded, grader_note) VALUES (?, ?, ?, ?)",
        (digest_id, time.time(), grounded, note),
    )
    con.commit()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(include_graded=False, verbose=True):
    con = _open_db()
    rows = _gradable_digests(con, include_graded)
    if not rows:
        print("No digests to grade (none stored with snippets yet, or all already graded).")
        con.close()
        return 0, 0

    graded = skipped = 0
    for row in rows:
        if verbose:
            when = datetime.fromtimestamp(row["ts"]).strftime("%Y-%m-%d %H:%M")
            print(f"[digest #{row['id']} @ {when}] grading...", flush=True)
        grounded, note = _grade(row["snippets"], row["content"])
        if grounded is None and note.startswith("grader call failed"):
            if verbose:
                print(f"  skipped: {note}")
            skipped += 1
            continue
        _save(con, row["id"], grounded, note)
        verdict = {1: "GROUNDED", 0: "UNGROUNDED", None: "UNSURE"}[grounded]
        if verbose:
            print(f"  -> {verdict}: {note}")
        graded += 1

    con.close()
    print(f"\nDone: {graded} graded, {skipped} skipped.")
    return graded, skipped


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report(days=30):
    con = _open_db()
    cutoff = time.time() - days * 86400
    rows = con.execute(
        "SELECT grounded FROM news_probes WHERE ts >= ?", (cutoff,)
    ).fetchall()
    ungrounded = con.execute(
        "SELECT news_probes.ts AS ts, news_probes.grader_note AS grader_note, "
        "world_news.content AS content "
        "FROM news_probes JOIN world_news ON world_news.id = news_probes.digest_id "
        "WHERE news_probes.grounded = 0 AND news_probes.ts >= ? "
        "ORDER BY news_probes.ts DESC LIMIT 10",
        (cutoff,),
    ).fetchall()
    con.close()

    total = len(rows)
    grounded_n = sum(1 for r in rows if r["grounded"] == 1)
    ungrounded_n = sum(1 for r in rows if r["grounded"] == 0)
    unsure_n = sum(1 for r in rows if r["grounded"] is None)
    judged = grounded_n + ungrounded_n
    rate = (grounded_n / judged * 100) if judged else float("nan")

    print(f"World-news faithfulness — last {days} days")
    print("=" * 60)
    if not total:
        print("No probes recorded yet.")
    else:
        print(f"  {total} digest(s) graded   grounded {grounded_n}/{judged} ({rate:.0f}%)   unsure {unsure_n}")

    print()
    print(f"Most recent UNGROUNDED ({len(ungrounded)}):")
    if not ungrounded:
        print("  (none)")
    for r in ungrounded:
        when = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M")
        print(f"  [{when}] {r['grader_note']}")
        print(f"      digest: {r['content'][:150]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Zeev world-news faithfulness probe")
    parser.add_argument("--all", action="store_true",
                         help="Re-grade every stored digest, not just ungraded ones")
    parser.add_argument("--report", action="store_true",
                         help="Print the faithfulness report and exit (no grading run)")
    parser.add_argument("--report-days", type=int, default=30,
                         help="Rolling window in days for --report (default 30)")
    args = parser.parse_args()

    if args.report:
        report(days=args.report_days)
        return

    graded, skipped = run(include_graded=args.all)
    sys.exit(0 if graded > 0 or skipped == 0 else 1)


if __name__ == "__main__":
    main()
