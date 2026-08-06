"""Outbound-call outcome tracking (call_outcomes table + ambient prompt block).

bt_call_loop runs in a background thread and its real result (voicemail,
live conversation, hung up, no answer) never reached any later chat turn at
all -- a follow-up like "did you get to make the call?" had nothing true to
draw on. Found live 2026-08-05: the model fabricated a confident "yes,
connected, had a pleasant conversation", then, once caught, a false "I can't
physically make calls" -- neither grounded in anything, because nothing was
ever recorded anywhere the chat LLM could see.

Deliberately its OWN table, never `messages` -- same reasoning as `dreams`
(see test_dreams.py): a fabricated line in `messages` gets embedded and
served back later as fact, which is the exact failure this exists to avoid
reproducing.
"""
import time

import pytest


@pytest.fixture
def db(zeev, tmp_path, monkeypatch):
    monkeypatch.setattr(zeev, "ZEEV_DB", tmp_path / "c.db")
    monkeypatch.setattr(zeev, "_db_con", None)
    yield zeev
    try:
        zeev._db_con.close()
    except Exception:
        pass
    zeev._db_con = None


def test_no_outcome_yet_returns_none(db):
    assert db.recent_call_outcome() is None


def test_log_and_read_round_trip(db):
    db.log_call_outcome("857-701-7252", "reached voicemail and left a message")
    got = db.recent_call_outcome()
    assert got == {"number": "857-701-7252", "outcome": "reached voicemail and left a message"}


def test_stale_outcome_is_dropped(db):
    db.log_call_outcome("555-1234", "spoke with a live person")
    with db._db_lock:
        db._db().execute("UPDATE call_outcomes SET ts = ?", (time.time() - 3600,))
        db._db().commit()
    assert db.recent_call_outcome(max_age=1800) is None


def test_only_the_most_recent_outcome_is_returned(db):
    db.log_call_outcome("111", "reached voicemail and left a message")
    db.log_call_outcome("222", "spoke with a live person (2 exchanges), call ended normally")
    got = db.recent_call_outcome()
    assert got["number"] == "222"


def test_missing_table_fails_open_not_raises(zeev, monkeypatch):
    """A DB error here must not take down the whole system prompt build --
    same reasoning as torah_search's try/except."""
    class _BrokenCon:
        def execute(self, *a, **kw):
            raise Exception("no such table: call_outcomes")
    monkeypatch.setattr(zeev, "_db", lambda: _BrokenCon())
    assert zeev.recent_call_outcome() is None


# ---------------------------------------------------------------------------
# Ambient system-prompt block
# ---------------------------------------------------------------------------

def test_prompt_includes_block_when_recent(db):
    db.log_call_outcome("857-701-7252", "reached voicemail and left a message")
    p = db._build_system_prompt("hello")
    assert "## Recent phone call: dialed 857-701-7252" in p
    assert "reached voicemail and left a message" in p


def test_prompt_omits_block_when_nothing_logged(db):
    p = db._build_system_prompt("hello")
    assert "## Recent phone call:" not in p


def test_prompt_omits_block_when_stale(db):
    db.log_call_outcome("857-701-7252", "reached voicemail and left a message")
    with db._db_lock:
        db._db().execute("UPDATE call_outcomes SET ts = ?",
                          (time.time() - db._CALL_OUTCOME_AMBIENT_WINDOW - 60,))
        db._db().commit()
    p = db._build_system_prompt("hello")
    assert "## Recent phone call:" not in p
