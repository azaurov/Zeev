#!/usr/bin/env python3
"""
Standalone PiSugar battery sampler for ragnarok.

Run every 5 min via zeev-battery-log.timer (see .claude/skills/add-zeev-timer).
Appends one (ts, level, charging, voltage, idle_sec, active_sec) row to its
own small SQLite db -- deliberately separate from zeev.db, same isolation
reasoning CLAUDE.md documents for call_outcomes/dreams: this is monitoring
data, nothing that should ever be read back by RAG/chat.

Does NOT import zeev.py -- that would pull in camera/audio/thermal probing
on every single 5-min tick just to read one socket. Talks to the PiSugar
socket directly, same protocol as zeev.py's own _pisugar_query().

idle_sec/active_sec come from zeev.py's face_state_totals.json (see its
"Face-state time totals" section) -- real ground truth of how much of this
5-min tick Zeev's device mode actually spent listening/thinking/speaking
vs sitting idle, not a CPU-time guess. Missing/stale (zeev-device not
running, or never produced a state transition yet) degrades to NULL for
both rather than a guessed split.
"""
import json
import socket
import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "battery_log.db"
STATE_TOTALS_PATH = Path(__file__).resolve().parent / "data" / "face_state_totals.json"
RETAIN_DAYS = 30

# Keys in face_state_totals.json's "totals" that count as Zeev actually
# doing something, vs "idle"/"ready" (screen on, waiting for a wake word --
# not nothing, but not the cost centers a "what's eating the battery"
# question is really asking about).
ACTIVE_STATES = {"listening", "thinking", "speaking", "error"}
IDLE_STATES = {"idle", "ready"}


def pisugar_query(cmd, timeout=2):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("127.0.0.1", 8423))
        s.sendall((cmd + "\n").encode())
        data = s.recv(64).decode().strip()
        s.close()
        return data
    except Exception:
        return None


def read_effective_state_totals(path=STATE_TOTALS_PATH, now=None):
    """Read zeev.py's persisted per-state totals and add the still-ongoing
    partial for whichever state is current -- same counter-plus-live-partial
    contract systemd uses for CPUUsageNSec. Returns None if the file is
    missing/corrupt (zeev-device never running, or not yet written once)."""
    now = now if now is not None else time.time()
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return None
    totals = dict(data.get("totals") or {})
    cur_state = data.get("current_state")
    cur_since = data.get("current_since")
    if cur_state is not None and cur_since is not None:
        totals[cur_state] = totals.get(cur_state, 0.0) + max(0.0, now - cur_since)
    return totals


def _idle_active_deltas(conn, effective_totals):
    """Diff effective_totals against the last-seen snapshot (persisted in
    its own table) to get this tick's idle/active seconds, then update the
    snapshot. First-ever observation of a state yields a zero delta (no
    baseline to diff against yet) rather than dumping its whole prior
    history into one tick."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS state_totals_snapshot (state TEXT PRIMARY KEY, seconds REAL)"
    )
    prev = dict(conn.execute("SELECT state, seconds FROM state_totals_snapshot").fetchall())

    idle_sec = 0.0
    active_sec = 0.0
    for state, secs in effective_totals.items():
        delta = max(0.0, secs - prev.get(state, secs))
        if state in ACTIVE_STATES:
            active_sec += delta
        elif state in IDLE_STATES:
            idle_sec += delta
        conn.execute(
            "INSERT INTO state_totals_snapshot (state, seconds) VALUES (?, ?) "
            "ON CONFLICT(state) DO UPDATE SET seconds = excluded.seconds",
            (state, secs),
        )
    return idle_sec, active_sec


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS battery_samples ("
        " ts REAL NOT NULL, level REAL, charging INTEGER, voltage REAL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_battery_ts ON battery_samples(ts)")
    # Added after the table first shipped -- guarded ALTER for the DB this
    # timer has already been writing to on ragnarok since the earlier build.
    for col in ("idle_sec", "active_sec"):
        try:
            conn.execute(f"ALTER TABLE battery_samples ADD COLUMN {col} REAL")
        except sqlite3.OperationalError:
            pass

    level_resp = pisugar_query("get battery")
    if not level_resp or "I2C not connected" in level_resp:
        print("[battery_log] PiSugar unreadable, skipping sample", flush=True)
        conn.close()
        return 0

    try:
        level = float(level_resp.split(": ")[1])
    except (IndexError, ValueError):
        print(f"[battery_log] unparseable level response: {level_resp!r}", flush=True)
        conn.close()
        return 0

    charge_resp = pisugar_query("get battery_charging")
    charging = None
    if charge_resp:
        try:
            charging = charge_resp.split(": ")[1].strip().lower() == "true"
        except IndexError:
            pass

    volt_resp = pisugar_query("get battery_v")
    voltage = None
    if volt_resp:
        try:
            voltage = float(volt_resp.split(": ")[1])
        except (IndexError, ValueError):
            pass

    now = time.time()

    idle_sec = active_sec = None
    effective_totals = read_effective_state_totals(now=now)
    if effective_totals is not None:
        try:
            idle_sec, active_sec = _idle_active_deltas(conn, effective_totals)
        except Exception as e:
            print(f"[battery_log] state-delta bookkeeping failed: {e}", flush=True)
            idle_sec = active_sec = None

    conn.execute(
        "INSERT INTO battery_samples (ts, level, charging, voltage, idle_sec, active_sec) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now, level, (1 if charging else 0) if charging is not None else None,
         voltage, idle_sec, active_sec),
    )
    conn.execute("DELETE FROM battery_samples WHERE ts < ?", (now - RETAIN_DAYS * 86400,))
    conn.commit()
    conn.close()
    print(f"[battery_log] {level:.1f}% charging={charging} v={voltage} "
          f"idle_sec={idle_sec} active_sec={active_sec}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
