#!/usr/bin/env python3
"""
migrate_to_sqlite.py — Idempotent migration of Zeev flat-file stores → zeev.db

Safe to run multiple times: uses INSERT OR IGNORE on a unique key per table so
existing rows are never duplicated. Original flat files are NOT modified.

Usage:
    python3 zeev/migrate_to_sqlite.py [--db PATH]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

HISTORY_FILE  = DATA_DIR / "history.jsonl"
MEMORY_FILE   = DATA_DIR / "user_memory.json"
NOTES_FILE    = DATA_DIR / "notes.jsonl"
SETTINGS_FILE = DATA_DIR / "settings.json"


def open_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def create_schema(con: sqlite3.Connection):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            role    TEXT    NOT NULL,
            content TEXT    NOT NULL,
            ts      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS facts (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT    NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS notes (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT    NOT NULL,
            ts   TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    con.commit()


def migrate_history(con: sqlite3.Connection) -> int:
    if not HISTORY_FILE.exists():
        print("  history.jsonl not found — skipping")
        return 0
    rows = []
    for line in HISTORY_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            role    = entry.get("role", "").strip()
            content = entry.get("content", "").strip()
            ts      = entry.get("ts", "")
            if role and content:
                rows.append((role, content, ts))
        except (json.JSONDecodeError, KeyError):
            pass

    # Check which rows are already in the DB (match on role+content+ts)
    existing = set(
        (r, c, t)
        for r, c, t in con.execute("SELECT role, content, ts FROM messages").fetchall()
    )
    new_rows = [r for r in rows if tuple(r) not in existing]
    if new_rows:
        con.executemany(
            "INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)",
            new_rows,
        )
        con.commit()
    print(f"  messages: {len(rows)} in file, {len(existing)} already in DB, {len(new_rows)} inserted")
    return len(new_rows)


def migrate_memory(con: sqlite3.Connection) -> int:
    if not MEMORY_FILE.exists():
        print("  user_memory.json not found — skipping")
        return 0
    try:
        data = json.loads(MEMORY_FILE.read_text())
        facts = [f.strip() for f in data if isinstance(f, str) and f.strip()]
    except (json.JSONDecodeError, ValueError):
        print("  user_memory.json unreadable — skipping")
        return 0

    inserted = 0
    for fact in facts:
        try:
            con.execute("INSERT OR IGNORE INTO facts (fact) VALUES (?)", (fact,))
            if con.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except sqlite3.Error:
            pass
    con.commit()
    print(f"  facts: {len(facts)} in file, {inserted} inserted (duplicates ignored)")
    return inserted


def migrate_notes(con: sqlite3.Connection) -> int:
    if not NOTES_FILE.exists():
        print("  notes.jsonl not found — skipping")
        return 0
    rows = []
    for line in NOTES_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            text = entry.get("text", "").strip()
            ts   = entry.get("ts", "")
            if text:
                rows.append((text, ts))
        except json.JSONDecodeError:
            pass

    existing = set(
        (t, s)
        for t, s in con.execute("SELECT text, ts FROM notes").fetchall()
    )
    new_rows = [r for r in rows if tuple(r) not in existing]
    if new_rows:
        con.executemany(
            "INSERT INTO notes (text, ts) VALUES (?, ?)",
            new_rows,
        )
        con.commit()
    print(f"  notes: {len(rows)} in file, {len(existing)} already in DB, {len(new_rows)} inserted")
    return len(new_rows)


def migrate_settings(con: sqlite3.Connection):
    if not SETTINGS_FILE.exists():
        print("  settings.json not found — using defaults")
        defaults = {"camera_flip": "false", "tts_on": "true"}
        for k, v in defaults.items():
            con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        con.commit()
        return
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except (json.JSONDecodeError, ValueError):
        print("  settings.json unreadable — skipping")
        return
    for k, v in data.items():
        con.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (str(k), json.dumps(v)),
        )
    con.commit()
    print(f"  settings: {list(data.keys())} migrated (existing keys preserved)")


def main():
    parser = argparse.ArgumentParser(description="Migrate Zeev flat files to zeev.db")
    parser.add_argument("--db", default=str(DATA_DIR / "zeev.db"), help="Path to target SQLite DB")
    args = parser.parse_args()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Target DB: {db_path}")
    con = open_db(db_path)
    create_schema(con)

    print("\nMigrating history...")
    migrate_history(con)

    print("\nMigrating memory facts...")
    migrate_memory(con)

    print("\nMigrating notes...")
    migrate_notes(con)

    print("\nMigrating settings...")
    migrate_settings(con)

    con.close()
    print("\nDone. Original flat files are untouched.")


if __name__ == "__main__":
    main()
