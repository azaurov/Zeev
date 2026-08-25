#!/usr/bin/env python3
"""
quantum_daily.py — Daily quantum circuit teaching for Zeev.

Runs one canonical human-dilemma scenario through the full quantum reasoning
pipeline and saves the insight to data/zeev.db (quantum_insights table).
Rotate through 8 scenarios by day-of-year so each one recurs ~45x/year.

Usage:
    python3 zeev/quantum_daily.py [--all]   # --all runs every scenario once
"""

import argparse
import json
import os
import re
import sqlite3
import sys
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
if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY not set", file=sys.stderr)
    sys.exit(1)

import requests
from quantum import quantum_reason

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
MODEL      = "qwen/qwen3.6-27b"
JSON_MODEL = "openai/gpt-oss-20b"

# qwen3.6-27b inlines its <think>...</think> reasoning into plain-text
# completions too, not just json_mode ones (see _llm_fn below) — without
# this, the interpretation step would store raw chain-of-thought straight
# into quantum_insights.interpretation. Same pattern as zeev.py's
# _strip_think_text / world_news.py's strip_think_text.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_UNCLOSED_RE = re.compile(r"<think>.*", re.DOTALL)


def _strip_think_text(text):
    if not text:
        return text
    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINK_UNCLOSED_RE.sub("", text)
    return text.strip()

# ---------------------------------------------------------------------------
# Canonical scenarios — timeless human dilemmas that map cleanly to circuits
# ---------------------------------------------------------------------------

SCENARIOS = [
    "Focus on one thing deeply versus explore many things broadly",
    "Tell the truth even when it hurts versus protect someone's feelings with silence",
    "Act on what I feel right now versus wait until I think it through completely",
    "Stay with what is familiar and safe versus step into the unknown",
    "Trust others with something important versus handle it alone",
    "Work harder now to build a better future versus enjoy what I have today",
    "Speak up in a conflict versus let it pass and preserve the peace",
    "Change course when something isn't working versus stay the course and push through",
]


def _llm_fn(msgs, max_tokens=400, json_mode=False):
    # Found live 2026-08-25 chasing a 12-day gap in quantum_insights: commit
    # 0720a50 (2026-08-14) migrated MODEL off a deprecated Groq model onto
    # qwen3.6-27b, which inlines a <think>...</think> reasoning block into
    # `content` instead of a separate field. That broke this call two ways:
    #
    # 1. json_mode circuit-mapping: Groq's response_format=json_object
    #    validator rejects a <think>-prefixed body outright
    #    (json_validate_failed, empty failed_generation) -- not fixable by
    #    stripping client-side, Groq never returns usable text at all. So
    #    json_mode routes to gpt-oss-20b instead, which keeps reasoning out
    #    of `content`, and response_format is never set regardless of model:
    #    with it set, gpt-oss-20b's own "phases" array comes back as one
    #    concatenated string ("0.53.61.4") instead of separate numbers --
    #    the identical prompt with no response_format returns a clean array.
    #    quantum.py's _llm_circuit_spec already does its own json.loads()
    #    on the text, so a plain completion is exactly what it needs.
    # 2. Plain interpretation calls on qwen3.6-27b: the model reasons so
    #    verbosely for this open-ended "interpret the pattern" prompt that
    #    it burns the entire token budget still inside <think> and never
    #    closes it -- verified live up to max_tokens=900, finish_reason
    #    "length" every time, so _strip_think_text correctly reduces it to
    #    "" (an unclosed <think> means no real answer was ever generated).
    #
    # reasoning_effort fixes both at the root instead of chasing budgets:
    # qwen3.6-27b only accepts "none"/"default" (not "low"), and "none"
    # suppresses the <think> block entirely (74 clean tokens, finish_reason
    # stop, verified live). gpt-oss-20b's "low" mirrors the same fix
    # news_digest.py already uses for this exact reasoning-eats-budget
    # pattern (46-258 reasoning tokens there; 22-25 seen here).
    model = JSON_MODEL if json_mode else MODEL
    payload = {
        "model": model,
        "messages": msgs,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "reasoning_effort": "low" if json_mode else "none",
    }
    try:
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        return _strip_think_text(r.json()["choices"][0]["message"]["content"]), None
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _open_db():
    db_path = BASE_DIR / "data" / "zeev.db"
    con = sqlite3.connect(str(db_path), check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS quantum_insights (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            idea           TEXT    NOT NULL,
            spec_json      TEXT    NOT NULL,
            result_json    TEXT    NOT NULL,
            interpretation TEXT    NOT NULL,
            ts             TEXT    NOT NULL
        )
    """)
    con.commit()
    return con


def _save_insight(con, idea, spec, result, interpretation):
    con.execute(
        "INSERT INTO quantum_insights (idea, spec_json, result_json, interpretation, ts) "
        "VALUES (?, ?, ?, ?, ?)",
        (idea, json.dumps(spec), json.dumps(result), interpretation, datetime.now().isoformat()),
    )
    con.commit()


def _count_insights(con):
    return con.execute("SELECT COUNT(*) FROM quantum_insights").fetchone()[0]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_scenario(con, idea, verbose=True):
    if verbose:
        print(f"\nScenario: {idea}")
        print("  Running quantum circuit…", flush=True)

    interpretation, spec, result, err = quantum_reason(idea, _llm_fn)

    if err:
        print(f"  ERROR: {err}")
        return False

    _save_insight(con, idea, spec, result, interpretation)

    if verbose:
        opts = spec.get("options", [])
        phases = spec.get("phases", [])
        print(f"  Options: {opts}")
        print(f"  Phases:  {[round(p, 2) for p in phases]}")
        top = list(result["probabilities"].items())[:3]
        print(f"  Top outcomes: {[(k, f'{v*100:.1f}%') for k, v in top]}")
        print(f"  Interpretation:")
        for line in interpretation.strip().split("\n"):
            print(f"    {line}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Zeev quantum daily teaching")
    parser.add_argument("--all", action="store_true", help="Run all 8 scenarios instead of today's")
    args = parser.parse_args()

    con = _open_db()
    before = _count_insights(con)

    if args.all:
        scenarios = SCENARIOS
        print(f"Running all {len(scenarios)} scenarios…")
    else:
        idx = datetime.now().timetuple().tm_yday % len(SCENARIOS)
        scenarios = [SCENARIOS[idx]]
        print(f"Daily quantum teaching — scenario {idx + 1}/{len(SCENARIOS)}")

    passed = sum(run_scenario(con, s) for s in scenarios)
    after = _count_insights(con)

    print(f"\n{'='*50}")
    print(f"Done: {passed}/{len(scenarios)} succeeded — "
          f"{after - before} insight(s) added (total: {after})")
    con.close()


if __name__ == "__main__":
    main()
