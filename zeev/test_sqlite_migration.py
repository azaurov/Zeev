#!/usr/bin/env python3
"""
test_sqlite_migration.py — Regression harness: flat-file vs SQLite storage

Runs the existing flat-file functions against the real data, then runs
equivalent SQLite functions against a freshly-migrated copy, and asserts
they produce identical output.

Usage:
    python3 zeev/test_sqlite_migration.py
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# Add zeev dir to path so we can import helpers
sys.path.insert(0, str(BASE_DIR))

# ── flat-file paths ─────────────────────────────────────────────────────────
HISTORY_FILE  = DATA_DIR / "history.jsonl"
MEMORY_FILE   = DATA_DIR / "user_memory.json"
NOTES_FILE    = DATA_DIR / "notes.jsonl"
SETTINGS_FILE = DATA_DIR / "settings.json"

PRIOR_TURNS = 15

_STOP_WORDS = frozenset([
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "was", "one", "our", "out", "get", "has", "him", "his", "how",
    "its", "may", "new", "now", "old", "see", "two", "who", "did",
    "let", "say", "she", "too", "use", "that", "this", "with", "have",
    "from", "they", "will", "been", "what", "when", "your", "than",
    "just", "more", "also", "into", "then", "some", "could", "would",
    "about", "there", "their", "which", "were", "does", "very", "like",
])


# ═══════════════════════════════════════════════════════════════════════════════
# Flat-file implementations (copied verbatim from zeev.py — the oracle)
# ═══════════════════════════════════════════════════════════════════════════════

def ff_load_prior():
    if not HISTORY_FILE.exists():
        return []
    lines = HISTORY_FILE.read_text().strip().splitlines()
    messages = []
    for line in lines[-(PRIOR_TURNS * 2):]:
        try:
            entry = json.loads(line)
            messages.append({"role": entry["role"], "content": entry["content"]})
        except (json.JSONDecodeError, KeyError):
            pass
    return messages


def ff_load_memory():
    if not MEMORY_FILE.exists():
        return []
    try:
        data = json.loads(MEMORY_FILE.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def ff_load_notes():
    if not NOTES_FILE.exists():
        return []
    notes = []
    for line in NOTES_FILE.read_text().splitlines():
        try:
            notes.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return notes


def ff_load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        return {
            "camera_flip": bool(data.get("camera_flip", False)),
            "tts_on":      bool(data.get("tts_on", True)),
        }
    except (FileNotFoundError, json.JSONDecodeError):
        return {"camera_flip": False, "tts_on": True}


def _tokenize(text):
    return {w for w in re.findall(r"\b[a-z]{3,}\b", text.lower()) if w not in _STOP_WORDS}


def ff_build_rag_index():
    entries, index = [], {}
    if not HISTORY_FILE.exists():
        return entries, index
    for line in HISTORY_FILE.read_text().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    for i, entry in enumerate(entries):
        for word in _tokenize(entry.get("content", "")):
            index.setdefault(word, []).append(i)
    return entries, index


def ff_retrieve_relevant(entries, index, query, k=2, min_score=2):
    if not index or not entries:
        return []
    scores = {}
    for word in _tokenize(query):
        for idx in index.get(word, []):
            scores[idx] = scores.get(idx, 0) + 1
    if not scores:
        return []
    ranked = sorted(scores, key=lambda i: -scores[i])
    pairs, seen = [], set()
    for idx in ranked:
        if scores[idx] < min_score or len(pairs) >= k:
            break
        if idx in seen:
            continue
        entry = entries[idx]
        role  = entry.get("role")
        if role == "user":
            seen.add(idx)
            for j in range(idx + 1, min(idx + 4, len(entries))):
                if entries[j].get("role") == "assistant" and j not in seen:
                    pairs.append((entry["content"], entries[j]["content"]))
                    seen.add(j)
                    break
        elif role == "assistant":
            seen.add(idx)
            for j in range(idx - 1, max(idx - 4, -1), -1):
                if entries[j].get("role") == "user" and j not in seen:
                    pairs.append((entries[j]["content"], entry["content"]))
                    seen.add(j)
                    break
    return pairs


# ═══════════════════════════════════════════════════════════════════════════════
# SQLite implementations (what will replace the flat-file ones post-cutover)
# ═══════════════════════════════════════════════════════════════════════════════

def db_open(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    return con


def db_load_prior(con: sqlite3.Connection):
    rows = con.execute(
        "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
        (PRIOR_TURNS * 2,),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def db_load_memory(con: sqlite3.Connection):
    rows = con.execute("SELECT fact FROM facts ORDER BY id").fetchall()
    return [r["fact"] for r in rows]


def db_load_notes(con: sqlite3.Connection):
    rows = con.execute("SELECT text, ts FROM notes ORDER BY id").fetchall()
    return [{"text": r["text"], "ts": r["ts"]} for r in rows]


def db_load_settings(con: sqlite3.Connection):
    rows = con.execute("SELECT key, value FROM settings").fetchall()
    raw = {r["key"]: json.loads(r["value"]) for r in rows}
    return {
        "camera_flip": bool(raw.get("camera_flip", False)),
        "tts_on":      bool(raw.get("tts_on", True)),
    }


def db_build_rag_index(con: sqlite3.Connection):
    """Build the same in-memory RAG index from SQLite messages."""
    entries, index = [], {}
    rows = con.execute("SELECT role, content, ts FROM messages ORDER BY id").fetchall()
    for row in rows:
        entries.append({"role": row["role"], "content": row["content"], "ts": row["ts"]})
    for i, entry in enumerate(entries):
        for word in _tokenize(entry.get("content", "")):
            index.setdefault(word, []).append(i)
    return entries, index


# ═══════════════════════════════════════════════════════════════════════════════
# Write round-trip tests (append → reload in an isolated temp DB)
# ═══════════════════════════════════════════════════════════════════════════════

def _create_temp_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL, content TEXT NOT NULL, ts TEXT NOT NULL
        );
        CREATE TABLE facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT NOT NULL UNIQUE
        );
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL, ts TEXT NOT NULL
        );
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    con.commit()
    return con


def db_append_message(con, role, content):
    con.execute(
        "INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)",
        (role, content, datetime.now().isoformat()),
    )
    con.commit()


def db_save_memory(con, facts):
    con.execute("DELETE FROM facts")
    for f in facts:
        con.execute("INSERT INTO facts (fact) VALUES (?)", (f,))
    con.commit()


def db_add_note(con, text):
    con.execute(
        "INSERT INTO notes (text, ts) VALUES (?, ?)",
        (text.strip(), datetime.now().isoformat()),
    )
    con.commit()


def db_delete_note(con, idx):
    rows = con.execute("SELECT id FROM notes ORDER BY id").fetchall()
    if not (0 <= idx < len(rows)):
        return False
    con.execute("DELETE FROM notes WHERE id = ?", (rows[idx]["id"],))
    con.commit()
    return True


def db_save_settings(con, camera_flip, tts_on):
    for k, v in [("camera_flip", camera_flip), ("tts_on", tts_on)]:
        con.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (k, json.dumps(v)),
        )
    con.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════════════════════

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results = []


def check(name, actual, expected, show_len=False):
    ok = actual == expected
    tag = PASS if ok else FAIL
    if show_len:
        print(f"  [{tag}] {name}  ({len(actual)} items)")
    else:
        print(f"  [{tag}] {name}")
    if not ok:
        print(f"         expected: {repr(expected)[:200]}")
        print(f"         actual:   {repr(actual)[:200]}")
    results.append((name, ok))


def run_read_tests(con):
    print("\n── Read equivalence (flat-file vs SQLite) ──────────────────────────")

    # load_prior
    ff = ff_load_prior()
    db = db_load_prior(con)
    check("load_prior: length", len(db), len(ff))
    check("load_prior: content matches", db, ff, show_len=True)

    # load_memory
    ff = ff_load_memory()
    db = db_load_memory(con)
    check("load_memory: matches", db, ff, show_len=True)

    # load_notes
    ff = ff_load_notes()
    db = db_load_notes(con)
    check("load_notes: length", len(db), len(ff))
    if ff and db:
        check("load_notes: first note text", db[0]["text"], ff[0]["text"])
        check("load_notes: last note text",  db[-1]["text"], ff[-1]["text"])
    check("load_notes: full match", db, ff, show_len=True)

    # load_settings
    ff = ff_load_settings()
    db = db_load_settings(con)
    check("load_settings: camera_flip", db["camera_flip"], ff["camera_flip"])
    check("load_settings: tts_on",      db["tts_on"],      ff["tts_on"])

    # RAG index
    print("\n── RAG equivalence ─────────────────────────────────────────────────")
    ff_entries, ff_index = ff_build_rag_index()
    db_entries, db_index = db_build_rag_index(con)
    check("rag: entry count", len(db_entries), len(ff_entries))
    check("rag: index key count", len(db_index), len(ff_index))
    if ff_entries and db_entries:
        check("rag: first entry role",    db_entries[0]["role"],    ff_entries[0]["role"])
        check("rag: first entry content", db_entries[0]["content"], ff_entries[0]["content"])
        check("rag: last entry role",     db_entries[-1]["role"],   ff_entries[-1]["role"])
        check("rag: last entry content",  db_entries[-1]["content"],ff_entries[-1]["content"])

    # retrieve_relevant on a handful of real queries
    sample_queries = [
        "what is the weather today",
        "tell me about Torah and Talmud",
        "explain the algorithm",
        "Alex Zaurov preferences",
        "music jazz playlist",
        "Hebrew prayer morning",
        "how do I fix the Python code",
    ]
    print("\n── retrieve_relevant equivalence ───────────────────────────────────")
    for q in sample_queries:
        ff_pairs = ff_retrieve_relevant(ff_entries, ff_index, q)
        db_pairs = ff_retrieve_relevant(db_entries, db_index, q)  # same algorithm, different source
        check(f"retrieve_relevant: {repr(q[:30])}", db_pairs, ff_pairs)


def run_write_tests():
    print("\n── Write round-trips (isolated in-memory DB) ───────────────────────")
    con = _create_temp_db()

    # messages
    db_append_message(con, "user", "Hello world")
    db_append_message(con, "assistant", "Hi there")
    db_append_message(con, "user", "Second message")
    msgs = db_load_prior(con)
    check("append+load: count", len(msgs), 3)
    check("append+load: order preserved", [m["role"] for m in msgs], ["user", "assistant", "user"])
    check("append+load: content", msgs[0]["content"], "Hello world")

    # load_prior respects PRIOR_TURNS*2 limit
    con2 = _create_temp_db()
    for i in range(40):
        db_append_message(con2, "user" if i % 2 == 0 else "assistant", f"msg {i}")
    loaded = db_load_prior(con2)
    check("load_prior: respects PRIOR_TURNS*2 limit", len(loaded), PRIOR_TURNS * 2)
    check("load_prior: last entry is newest", loaded[-1]["content"], "msg 39")

    # facts
    db_save_memory(con, ["likes jazz", "lives in Seattle", "has a dog"])
    facts = db_load_memory(con)
    check("save+load memory: count", len(facts), 3)
    check("save+load memory: order preserved", facts, ["likes jazz", "lives in Seattle", "has a dog"])
    # overwrite
    db_save_memory(con, ["new fact"])
    facts = db_load_memory(con)
    check("save memory: overwrites correctly", facts, ["new fact"])

    # notes
    db_add_note(con, "buy milk")
    db_add_note(con, "call mom")
    db_add_note(con, "fix the bug")
    notes = db_load_notes(con)
    check("add+load notes: count", len(notes), 3)
    check("add+load notes: text order", [n["text"] for n in notes], ["buy milk", "call mom", "fix the bug"])
    # delete middle note
    db_delete_note(con, 1)
    notes = db_load_notes(con)
    check("delete_note: count after delete", len(notes), 2)
    check("delete_note: remaining items", [n["text"] for n in notes], ["buy milk", "fix the bug"])
    # delete out of range
    ok = db_delete_note(con, 99)
    check("delete_note: out-of-range returns False", ok, False)

    # settings
    db_save_settings(con, camera_flip=True, tts_on=False)
    s = db_load_settings(con)
    check("save+load settings: camera_flip", s["camera_flip"], True)
    check("save+load settings: tts_on",      s["tts_on"],      False)
    # update
    db_save_settings(con, camera_flip=False, tts_on=True)
    s = db_load_settings(con)
    check("update settings: camera_flip", s["camera_flip"], False)
    check("update settings: tts_on",      s["tts_on"],      True)

    # threading: concurrent appends must not corrupt
    con3 = _create_temp_db()
    lock = threading.Lock()

    def writer(n):
        for i in range(10):
            with lock:
                db_append_message(con3, "user", f"thread {n} msg {i}")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    count = con3.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    check("threading: all 50 messages written", count, 50)


def main():
    print("Zeev SQLite migration regression tests")
    print("=" * 50)

    db_path = DATA_DIR / "zeev.db"
    if not db_path.exists():
        print(f"\nERROR: {db_path} not found.")
        print("Run first:  python3 zeev/migrate_to_sqlite.py")
        sys.exit(1)

    con = db_open(db_path)
    run_read_tests(con)
    con.close()

    run_write_tests()

    total  = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed

    print(f"\n{'=' * 50}")
    print(f"Results: {passed}/{total} passed", end="")
    if failed:
        print(f"  ← {failed} FAILED")
        print("\nFailed tests:")
        for name, ok in results:
            if not ok:
                print(f"  • {name}")
        sys.exit(1)
    else:
        print(" — all good")
        print("\nReady for cutover. Run with Alex's approval:")
        print("  python3 zeev/cutover_to_sqlite.py")


if __name__ == "__main__":
    main()
