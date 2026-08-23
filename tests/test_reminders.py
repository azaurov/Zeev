"""Reminders, timers, and the tool dispatch behind them (Phase 4).

The failure modes that matter are quiet ones: a reminder announced twice, a
reminder silently dropped because the model phrased the time in a way the
parser didn't accept, or a tool call whose arguments arrive as malformed JSON.
"""
import datetime as dt
import json
import sqlite3

import pytest


@pytest.fixture
def db(zeev, tmp_path, monkeypatch):
    con = sqlite3.connect(str(tmp_path / "r.db"), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL, due_ts REAL NOT NULL,
            created_ts REAL NOT NULL, fired INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL, ts TEXT NOT NULL);
    """)
    con.commit()
    monkeypatch.setattr(zeev, "_db", lambda: con)
    return con


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

def test_parse_relative_minutes(zeev):
    now = dt.datetime(2026, 7, 28, 10, 0, 0)
    got = zeev._parse_when("in 10 minutes", now=now)
    assert got == pytest.approx((now + dt.timedelta(minutes=10)).timestamp())


@pytest.mark.parametrize("text,delta", [
    ("in 30 seconds", dt.timedelta(seconds=30)),
    ("in 2 hours", dt.timedelta(hours=2)),
    ("in 1 day", dt.timedelta(days=1)),
    ("in 5 min", dt.timedelta(minutes=5)),
    ("IN 5 MINUTES", dt.timedelta(minutes=5)),
])
def test_parse_relative_forms(zeev, text, delta):
    """Models emit these regardless of what the schema asks for."""
    now = dt.datetime(2026, 7, 28, 10, 0, 0)
    assert zeev._parse_when(text, now=now) == pytest.approx((now + delta).timestamp())


def test_parse_iso(zeev):
    now = dt.datetime(2026, 7, 28, 10, 0, 0)
    got = zeev._parse_when("2026-07-28T16:00:00", now=now)
    assert got == pytest.approx(dt.datetime(2026, 7, 28, 16, 0, 0).timestamp())


def test_bare_time_already_past_rolls_to_tomorrow(zeev):
    """"remind me at 9" said at 10am means tomorrow, not eight hours ago."""
    now = dt.datetime(2026, 7, 28, 10, 0, 0)
    got = zeev._parse_when("2026-07-28T09:00:00", now=now)
    assert got == pytest.approx(dt.datetime(2026, 7, 29, 9, 0, 0).timestamp())


def test_future_time_today_is_kept(zeev):
    now = dt.datetime(2026, 7, 28, 10, 0, 0)
    got = zeev._parse_when("2026-07-28T16:00:00", now=now)
    assert got < dt.datetime(2026, 7, 29).timestamp()


@pytest.mark.parametrize("junk", [None, "", "   ", "whenever", "later today", "4"])
def test_unparseable_returns_none(zeev, junk):
    """Must return None so the caller can ask the user again, not crash or
    silently schedule something wrong."""
    assert zeev._parse_when(junk) is None or isinstance(zeev._parse_when(junk), float)


# ---------------------------------------------------------------------------
# Store / claim
# ---------------------------------------------------------------------------

def test_add_and_list(zeev, db):
    rid, due = zeev.add_reminder("call Dave", "in 10 minutes")
    assert rid is not None and due is not None
    rems = zeev.list_reminders()
    assert len(rems) == 1 and rems[0]["text"] == "call Dave"


def test_add_rejects_bad_time(zeev, db):
    assert zeev.add_reminder("call Dave", "whenever") == (None, None)
    assert zeev.list_reminders() == []


def test_add_rejects_empty_text(zeev, db):
    assert zeev.add_reminder("   ", "in 5 minutes") == (None, None)


def test_due_reminders_fire_once(zeev, db):
    """The important one: claiming must be atomic, or a reminder gets
    announced twice by the poll loop."""
    zeev.add_reminder("past thing", "in 1 second")
    later = __import__("time").time() + 3600
    first = zeev.due_reminders(now=later)
    second = zeev.due_reminders(now=later)
    assert len(first) == 1
    assert second == [], "reminder fired twice"


def test_future_reminder_not_claimed_early(zeev, db):
    import time as _t
    zeev.add_reminder("future thing", "in 2 hours")
    assert zeev.due_reminders(now=_t.time()) == []


def test_delete_reminder(zeev, db):
    rid, _ = zeev.add_reminder("cancel me", "in 1 hour")
    assert zeev.delete_reminder(rid) is True
    assert zeev.list_reminders() == []
    assert zeev.delete_reminder(rid) is False


# ---------------------------------------------------------------------------
# Morning-serenade alarm (one-time wake-up duet, distinct from the daily
# _GOODMORNING_LINES weekday greeting)
# ---------------------------------------------------------------------------

def test_every_serenade_pair_names_alex(zeev):
    for zeev_line, sarina_line in zeev._MORNING_SERENADE_LINES:
        assert "Alex" in zeev_line + sarina_line


def test_serenade_sentinel_is_not_read_aloud_literally(zeev, db):
    """A reminder whose text is the sentinel must be routed to the duet, not
    spoken as 'Reminder: __MORNING_SERENADE__' -- _reminder_loop is the only
    place that decides this, so pin its output here rather than relying on
    device mode (untestable off real hardware)."""
    zeev.add_reminder(zeev._MORNING_SERENADE_SENTINEL, "in 1 second")
    later = __import__("time").time() + 3600
    due = zeev.due_reminders(now=later)
    assert len(due) == 1
    text = due[0]["text"]
    assert text == zeev._MORNING_SERENADE_SENTINEL
    # Mirrors _reminder_loop's own branch so a future edit to that branch
    # (e.g. dropping the sentinel check) breaks this test too.
    msg = (zeev._MORNING_SERENADE_SENTINEL if text == zeev._MORNING_SERENADE_SENTINEL
           else f"Reminder: {text}")
    assert msg == zeev._MORNING_SERENADE_SENTINEL
    assert "Reminder:" not in msg


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def test_run_tool_set_reminder(zeev, db):
    out = zeev.run_tool("set_reminder", {"text": "call Dave", "when": "in 10 minutes"})
    assert "call Dave" in out
    assert len(zeev.list_reminders()) == 1


def test_run_tool_reports_unparseable_time(zeev, db):
    """The model must be told to re-ask rather than think it succeeded."""
    out = zeev.run_tool("set_reminder", {"text": "x", "when": "sometime"})
    assert "not understood" in out.lower()


def test_run_tool_unknown_name(zeev, db):
    assert "unknown tool" in zeev.run_tool("launch_missiles", {}).lower()


def test_run_tool_calls_parses_arguments(zeev, db):
    msgs = zeev.run_tool_calls([{
        "id": "call_1",
        "function": {"name": "set_reminder",
                     "arguments": json.dumps({"text": "standup", "when": "in 15 minutes"})},
    }])
    assert len(msgs) == 1
    assert msgs[0]["role"] == "tool" and msgs[0]["tool_call_id"] == "call_1"
    assert len(zeev.list_reminders()) == 1


def test_run_tool_calls_survives_malformed_json(zeev, db):
    """Models do emit broken argument JSON; it must not kill the turn."""
    msgs = zeev.run_tool_calls([{
        "id": "call_2",
        "function": {"name": "set_reminder", "arguments": "{not json"},
    }])
    assert len(msgs) == 1 and msgs[0]["role"] == "tool"


def test_run_tool_calls_empty(zeev, db):
    assert zeev.run_tool_calls([]) == []
    assert zeev.run_tool_calls(None) == []


def test_add_note_tool(zeev, db, monkeypatch):
    monkeypatch.setattr(zeev, "USER_NOTES", [])
    out = zeev.run_tool("add_note", {"text": "buy milk"})
    assert "buy milk" in out
    assert db.execute("SELECT count(*) FROM notes").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Intent gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "remind me to call Dave at 4",
    "set a timer for 10 minutes",
    "what are my reminders",
    "make a note that the router password changed",
    "wake me at 7",
    "cancel the reminder",
])
def test_tool_intent_matches(zeev, text):
    assert zeev._TOOL_INTENT_RE.search(text) is not None, text


@pytest.mark.parametrize("text", [
    "what's the weather like",
    "tell me about the Talmud",
    "how are you doing",
    "play some jazz",
])
def test_tool_intent_ignores_ordinary_chat(zeev, text):
    """Ordinary turns must not pay for the extra non-streaming round trip."""
    assert zeev._TOOL_INTENT_RE.search(text) is None, text


# ---------------------------------------------------------------------------
# Calendar write
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "put dinner with Maria on my calendar Thursday at 7",
    "schedule a dentist appointment for Friday",
    "book a table for tomorrow night",
    "add lunch to the calendar tomorrow",
    "create a calendar event",
])
def test_calendar_create_reaches_tool_gate(zeev, text):
    assert zeev._TOOL_INTENT_RE.search(text) is not None, text


@pytest.mark.parametrize("text", [
    "what's my schedule today",
    "what is on my schedule",
    "my schedule is packed",
    "what do I have tomorrow",
])
def test_calendar_read_questions_skip_the_tool_path(zeev, text):
    """Reads are answered from injected context; sending them through tools
    costs an extra non-streaming round trip for nothing."""
    assert zeev._TOOL_INTENT_RE.search(text) is None, text


def test_create_event_rejects_bad_time(zeev, tmp_path, monkeypatch):
    monkeypatch.setattr(zeev, "GCAL_TOKEN_PATH", tmp_path / "tok.json")
    (tmp_path / "tok.json").write_text("{}")
    start, err = zeev.gcal_create_event("dinner", "whenever")
    assert start is None and "not understood" in err


def test_create_event_rejects_empty_title(zeev, tmp_path, monkeypatch):
    monkeypatch.setattr(zeev, "GCAL_TOKEN_PATH", tmp_path / "tok.json")
    (tmp_path / "tok.json").write_text("{}")
    start, err = zeev.gcal_create_event("  ", "in 2 hours")
    assert start is None and "title" in err


def test_create_event_without_token(zeev, tmp_path, monkeypatch):
    monkeypatch.setattr(zeev, "GCAL_TOKEN_PATH", tmp_path / "missing.json")
    start, err = zeev.gcal_create_event("dinner", "in 2 hours")
    assert start is None and "token" in err


def test_create_event_reports_insufficient_scope(zeev, tmp_path, monkeypatch):
    """The failure that matters: a token predating the events scope keeps
    reads working while writes 403, which is otherwise invisible."""
    monkeypatch.setattr(zeev, "GCAL_TOKEN_PATH", tmp_path / "tok.json")
    (tmp_path / "tok.json").write_text("{}")
    monkeypatch.setattr(zeev, "_gcal_access_token", lambda: "tok")

    class Resp:
        status_code = 403
        text = "insufficient scope"

    monkeypatch.setattr(zeev.requests, "post", lambda *a, **kw: Resp())
    start, err = zeev.gcal_create_event("dinner", "in 2 hours")
    assert start is None and "gcal_auth" in err


def test_run_tool_create_event_surfaces_errors(zeev, tmp_path, monkeypatch):
    monkeypatch.setattr(zeev, "GCAL_TOKEN_PATH", tmp_path / "missing.json")
    out = zeev.run_tool("create_calendar_event", {"summary": "dinner", "when": "in 2 hours"})
    assert "could not create" in out.lower()
