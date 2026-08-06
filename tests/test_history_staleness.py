"""Tests for staleness annotation on ordinary (non-vision) history RAG hits.

Found live 2026-08-06 via rag_probe.py: a month-old, already-concluded
"visiting Uncle Sasha" exchange was retrieved and stitched into a
present-tense answer to "what are your coordinates" as if still true. Same
failure class as the vision-staleness caveat in test_vision_grounding.py
(a retrieved moment read back as a standing fact), but for ordinary
conversation describing a temporary state rather than a camera frame.

_build_system_prompt attaches a bare date (not a computed "N days ago"
phrase) to any non-vision hit whose original message can be dated and isn't
from today -- the model already has today's date from the "## Right now"
block, so it can judge currency itself.
"""
import sqlite3

import pytest


def test_message_date_str_found(zeev, monkeypatch):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT, ts TEXT)")
    con.execute(
        "INSERT INTO messages VALUES (1, 'user', 'visiting Uncle Sasha', "
        "'2026-07-12T17:33:14.420070')"
    )
    con.commit()
    monkeypatch.setattr(zeev, "_db", lambda: con)

    assert zeev._message_date_str("visiting Uncle Sasha") == "2026-07-12"


def test_message_date_str_not_found_returns_none(zeev, monkeypatch):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT, ts TEXT)")
    con.commit()
    monkeypatch.setattr(zeev, "_db", lambda: con)

    assert zeev._message_date_str("never said this") is None


def test_message_date_str_bad_timestamp_fails_closed(zeev, monkeypatch):
    """A staleness annotation is a nice-to-have -- a malformed ts must not
    raise into a live turn, just decline to annotate."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT, ts TEXT)")
    con.execute("INSERT INTO messages VALUES (1, 'user', 'hi', 'not-a-timestamp')")
    con.commit()
    monkeypatch.setattr(zeev, "_db", lambda: con)

    assert zeev._message_date_str("hi") is None


# ---------------------------------------------------------------------------
# _build_system_prompt: attaches the caveat for stale, non-vision hits only
# ---------------------------------------------------------------------------

def test_stale_history_hit_gets_staleness_caveat(zeev, monkeypatch):
    hit = ("what are you up to this summer",
           "I'm currently visiting Uncle Sasha for the summer.")
    monkeypatch.setattr(zeev, "retrieve_semantic", lambda *a, **kw: [hit])
    monkeypatch.setattr(zeev, "_message_date_str", lambda content: "2026-07-12")

    p = zeev._build_system_prompt("what are your coordinates")

    assert "2026-07-12" in p
    assert "temporary or since-changed situation" in p


def test_same_day_history_hit_gets_no_staleness_caveat(zeev, monkeypatch):
    from datetime import datetime
    hit = ("what's up", "not much")
    monkeypatch.setattr(zeev, "retrieve_semantic", lambda *a, **kw: [hit])
    today = datetime.now().strftime("%Y-%m-%d")
    monkeypatch.setattr(zeev, "_message_date_str", lambda content: today)

    p = zeev._build_system_prompt("q")

    assert "temporary or since-changed situation" not in p


def test_undateable_history_hit_gets_no_staleness_caveat(zeev, monkeypatch):
    hit = ("what's up", "not much")
    monkeypatch.setattr(zeev, "retrieve_semantic", lambda *a, **kw: [hit])
    monkeypatch.setattr(zeev, "_message_date_str", lambda content: None)

    p = zeev._build_system_prompt("q")

    assert "temporary or since-changed situation" not in p


def test_vision_hit_does_not_also_get_staleness_caveat(zeev, monkeypatch):
    """Vision hits get their own caveat (test_vision_grounding.py) -- the
    elif branching must not stack a second, generic one on top of it."""
    hit = ("did you see Smokey earlier", zeev._VISION_TAG + "I can see a grey cat.")
    monkeypatch.setattr(zeev, "retrieve_semantic", lambda *a, **kw: [hit])
    monkeypatch.setattr(zeev, "_message_date_str", lambda content: "2020-01-01")

    p = zeev._build_system_prompt("is Smokey still there")

    assert "camera observation from a past moment" in p
    assert "temporary or since-changed situation" not in p
