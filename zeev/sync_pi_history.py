#!/usr/bin/env python3
"""Pull ragnarok's (device-mode) facts + new messages into this box's local
zeev.db, so the web-chat instance (serve_web.py) has some continuity with
what Alex has told device-mode Zeev.

Run by zeev-sync-pi.service every 6h; its ExecStartPost restarts zeev-web
so the reloaded USER_FACTS / RAG index actually gets picked up.

Read-only on the Pi side: copies its zeev.db over SSH to a temp file and
reads from that copy, never touches the live file in place. Facts are
deduped by exact text (matches the table's own UNIQUE constraint); messages
are synced high-water-mark style off the Pi's own row ids (stored in this
box's settings table) so a run only pulls what's new, mirroring the
"backfill new-only" pattern _memory_maintenance_loop already uses for
message vectors in zeev.py.
"""
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOCAL_DB = BASE_DIR / "data" / "zeev.db"
PI_HOST = "ragnar@ragnarok"
PI_DB_PATH = "/home/ragnar/Zeev/zeev/data/zeev.db"
SYNC_MARK_KEY = "sync_pi_last_message_id"


def fetch_pi_db(dest_path: Path) -> None:
    result = subprocess.run(
        ["scp", "-o", "ConnectTimeout=10", f"{PI_HOST}:{PI_DB_PATH}", str(dest_path)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"scp from {PI_HOST} failed: {result.stderr.strip()}")


def sync(pi_db_path: Path) -> tuple[int, int]:
    LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
    local = sqlite3.connect(str(LOCAL_DB))
    local.execute("PRAGMA journal_mode=WAL")
    local.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    local.execute(
        "CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, fact TEXT NOT NULL UNIQUE)"
    )
    local.execute(
        "CREATE TABLE IF NOT EXISTS messages "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, ts TEXT NOT NULL)"
    )

    pi = sqlite3.connect(f"file:{pi_db_path}?mode=ro", uri=True)

    facts_added = 0
    try:
        with local:
            for (fact,) in pi.execute("SELECT fact FROM facts"):
                cur = local.execute(
                    "INSERT OR IGNORE INTO facts (fact) VALUES (?)", (fact,)
                )
                facts_added += cur.rowcount

            row = local.execute(
                "SELECT value FROM settings WHERE key = ?", (SYNC_MARK_KEY,)
            ).fetchone()
            last_id = int(row[0]) if row else 0

            new_rows = pi.execute(
                "SELECT id, role, content, ts FROM messages WHERE id > ? ORDER BY id",
                (last_id,),
            ).fetchall()
            for pid, role, content, ts in new_rows:
                local.execute(
                    "INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)",
                    (role, content, ts),
                )
                last_id = pid

            if new_rows:
                local.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (SYNC_MARK_KEY, str(last_id)),
                )
    finally:
        pi.close()
        local.close()

    return facts_added, len(new_rows)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        pi_db_copy = Path(tmp) / "ragnarok_zeev.db"
        try:
            fetch_pi_db(pi_db_copy)
            facts_added, messages_added = sync(pi_db_copy)
        except Exception as e:
            print(f"[sync_pi_history] failed: {e}", file=sys.stderr)
            return 1

    print(f"[sync_pi_history] synced {facts_added} new facts, {messages_added} new messages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
