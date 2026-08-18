"""news_probe.py -- faithfulness grading for the world-news shpeel.

Mirrors rag_probe.py's grading shape (parse_grade, UNGROUNDED-first ordering)
applied to world_news digests instead of Torah/history RAG. Requires
GROQ_API_KEY at import time (same constraint news_digest.py has), so these
tests set a dummy one before importing if it's not already present in the
environment.
"""
import os
import sqlite3
import sys
import time
from pathlib import Path

os.environ.setdefault("GROQ_API_KEY", "test-key-not-real")

sys.path.insert(0, str(Path(__file__).parent.parent / "zeev"))
import news_probe  # noqa: E402


# --- parse_grade -------------------------------------------------------------

def test_parse_grade_grounded():
    grounded, note = news_probe.parse_grade("GROUNDED\nMatches the context exactly.")
    assert grounded == 1
    assert "Matches" in note


def test_parse_grade_ungrounded():
    grounded, note = news_probe.parse_grade("UNGROUNDED\nInvented a casualty figure not in context.")
    assert grounded == 0
    assert "casualty" in note


def test_parse_grade_unsure():
    grounded, note = news_probe.parse_grade("UNSURE\nCan't tell if this detail was implied.")
    assert grounded is None
    assert "detail" in note


def test_parse_grade_ungrounded_beats_grounded_substring():
    """"GROUNDED" is a literal substring of "UNGROUNDED" -- order must not
    let a naive containment check flip the verdict."""
    grounded, _ = news_probe.parse_grade("UNGROUNDED\nsome note")
    assert grounded == 0


def test_parse_grade_empty_response():
    grounded, note = news_probe.parse_grade("")
    assert grounded is None
    assert "empty" in note


def test_parse_grade_unparseable():
    grounded, note = news_probe.parse_grade("I'm not sure how to answer that.")
    assert grounded is None
    assert "unparseable" in note


# --- DB / gradable-digest selection -------------------------------------------

def _con(tmp_path, monkeypatch):
    monkeypatch.setattr(news_probe, "BASE_DIR", tmp_path)
    (tmp_path / "data").mkdir()
    return news_probe._open_db()


def test_open_db_adds_snippets_column_to_legacy_table(tmp_path, monkeypatch):
    """A world_news table created before `snippets` existed (the real
    pre-fix state on ragnarok) must gain the column, not error out."""
    monkeypatch.setattr(news_probe, "BASE_DIR", tmp_path)
    (tmp_path / "data").mkdir()
    db_path = tmp_path / "data" / "zeev.db"
    legacy = sqlite3.connect(str(db_path))
    legacy.execute("""
        CREATE TABLE world_news (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT    NOT NULL,
            ts      REAL    NOT NULL
        )
    """)
    legacy.commit()
    legacy.close()

    con = news_probe._open_db()
    cols = {r[1] for r in con.execute("PRAGMA table_info(world_news)").fetchall()}
    assert "snippets" in cols


def test_gradable_digests_excludes_null_snippets(tmp_path, monkeypatch):
    con = _con(tmp_path, monkeypatch)
    con.execute("INSERT INTO world_news (content, ts, snippets) VALUES (?, ?, NULL)",
                ("old-style digest, no snippets stored", time.time()))
    con.execute("INSERT INTO world_news (content, ts, snippets) VALUES (?, ?, ?)",
                ("new-style digest", time.time(), "some snippet text"))
    con.commit()

    rows = news_probe._gradable_digests(con)
    assert len(rows) == 1
    assert rows[0]["content"] == "new-style digest"


def test_gradable_digests_excludes_already_graded_by_default(tmp_path, monkeypatch):
    con = _con(tmp_path, monkeypatch)
    con.execute("INSERT INTO world_news (content, ts, snippets) VALUES (?, ?, ?)",
                ("digest one", time.time(), "snippets one"))
    con.commit()
    digest_id = con.execute("SELECT id FROM world_news").fetchone()["id"]
    con.execute("INSERT INTO news_probes (digest_id, ts, grounded, grader_note) VALUES (?, ?, 1, 'fine')",
                (digest_id, time.time()))
    con.commit()

    assert news_probe._gradable_digests(con) == []
    assert len(news_probe._gradable_digests(con, include_graded=True)) == 1
