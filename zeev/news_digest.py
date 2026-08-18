#!/usr/bin/env python3
"""news_digest.py — periodic curated world-news digest ("the shpeel").

Runs the curated query list from world_news.py through Tavily, summarizes
the results into a spoken briefing via LLM, and caches it in the
`world_news` table. zeev.py's "give me the shpeel" handler reads the latest
cached row, only falling back to a smaller live pull of its own if this
cache is missing or stale -- so this script is what keeps that path fast in
the common case.

Usage:
    python3 zeev/news_digest.py            # build and store a fresh digest
    python3 zeev/news_digest.py --show      # print the latest stored digest
"""

import argparse
import os
import sqlite3
import sys
import time
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

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
BOSGAME_URL    = os.environ.get("BOSGAME_URL", "")
BOSGAME_KEY    = os.environ.get("BOSGAME_KEY", "")

if not TAVILY_API_KEY:
    print("ERROR: TAVILY_API_KEY not set", file=sys.stderr)
    sys.exit(1)
if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY not set", file=sys.stderr)
    sys.exit(1)

import requests

from world_news import build_shpeel

TAVILY_URL = "https://api.tavily.com/search"
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "qwen/qwen3.6-27b"


# ---------------------------------------------------------------------------
# Search + LLM
# ---------------------------------------------------------------------------

def _tavily(query):
    resp = requests.post(
        TAVILY_URL,
        json={"api_key": TAVILY_API_KEY, "query": query, "max_results": 5},
        timeout=15,
    )
    results = resp.json().get("results", [])
    if not results:
        return ""
    return "\n\n".join(
        f"{r['title']}\n{r['url']}\n{r.get('content', '')}" for r in results
    )


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
                "max_tokens": 500,
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
                "max_tokens": 500,
                "temperature": 0.7,
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip(), None
    except Exception as e:
        return None, str(e)


def _llm(prompt):
    content, err = _call_bosgame(prompt)
    if content:
        print("  [LLM: bosgame]")
        return content, None
    print(f"  [bosgame failed: {err}] — trying Groq…")
    content, err = _call_groq(prompt)
    if content:
        print("  [LLM: Groq]")
        return content, None
    return None, err


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
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT    NOT NULL,
            ts      REAL    NOT NULL
        );
    """)
    con.commit()
    return con


def _save(con, content):
    con.execute(
        "INSERT INTO world_news (content, ts) VALUES (?, ?)",
        (content, time.time()),
    )
    con.commit()


def show_latest():
    con = _open_db()
    row = con.execute(
        "SELECT content, ts FROM world_news ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    if not row:
        print("No world-news digest stored yet.")
        return
    age_h = (time.time() - row["ts"]) / 3600
    print(f"Latest shpeel ({age_h:.1f}h old):\n\n{row['content']}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    print("Gathering world-news snippets…", flush=True)
    content, err = build_shpeel(_tavily, _llm)
    if err:
        print(f"ERROR: {err}")
        return False
    con = _open_db()
    _save(con, content)
    con.close()
    print(f"\n{'='*50}\nShpeel stored\n\n{content}\n")
    return True


def main():
    parser = argparse.ArgumentParser(description="Zeev curated world-news digest")
    parser.add_argument("--show", action="store_true",
                         help="Print the latest stored digest and exit")
    args = parser.parse_args()

    if args.show:
        show_latest()
        return

    success = run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
