"""Tests for retrieval-augmented personalization: recency-weighted ranking
and near-duplicate dedup in retrieve_semantic/retrieve_relevant.

See docs/research-directions.md #1 -- pure re-ranking of what the frozen
embedding model already retrieves, no new infra, no fine-tuning.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest


# ---------------------------------------------------------------------------
# _recency_factor
# ---------------------------------------------------------------------------

def test_recency_factor_now_is_one(zeev):
    assert zeev._recency_factor(datetime.now().isoformat()) == pytest.approx(1.0, abs=1e-6)


def test_recency_factor_future_is_one(zeev):
    """Clock skew between processes must never produce a >1.0 or negative bonus."""
    future = (datetime.now() + timedelta(days=5)).isoformat()
    assert zeev._recency_factor(future) == 1.0


def test_recency_factor_half_life(zeev):
    half_life = zeev._RAG_RECENCY_HALFLIFE_DAYS
    aged = (datetime.now() - timedelta(days=half_life)).isoformat()
    assert zeev._recency_factor(aged) == pytest.approx(0.5, rel=1e-3)


def test_recency_factor_decays_monotonically(zeev):
    now = datetime.now()
    f1 = zeev._recency_factor((now - timedelta(days=1)).isoformat())
    f30 = zeev._recency_factor((now - timedelta(days=30)).isoformat())
    f365 = zeev._recency_factor((now - timedelta(days=365)).isoformat())
    assert f1 > f30 > f365 > 0


def test_recency_factor_fails_open_on_bad_timestamp(zeev):
    """A ranking nudge must never become a hard exclusion just because a row
    has a missing/unparseable ts."""
    assert zeev._recency_factor(None) == 1.0
    assert zeev._recency_factor("") == 1.0
    assert zeev._recency_factor("not-a-date") == 1.0


# ---------------------------------------------------------------------------
# retrieve_semantic: recency reorders, but min_sim still gates on raw sim
# ---------------------------------------------------------------------------

def _fresh_rag_db(tmp_path):
    con = sqlite3.connect(str(tmp_path / "rag.db"), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL, content TEXT NOT NULL, ts TEXT NOT NULL
        );
        CREATE TABLE message_vecs (
            message_id INTEGER PRIMARY KEY REFERENCES messages(id),
            dim INTEGER NOT NULL, vec BLOB NOT NULL
        );
    """)
    con.commit()
    return con


def _seed(zeev, con, user_content, ts, vec, reply="ok"):
    cur = con.execute(
        "INSERT INTO messages (role, content, ts) VALUES ('user', ?, ?)",
        (user_content, ts),
    )
    mid = cur.lastrowid
    con.execute(
        "INSERT INTO messages (role, content, ts) VALUES ('assistant', ?, ?)",
        (reply, ts),
    )
    con.execute(
        "INSERT INTO message_vecs (message_id, dim, vec) VALUES (?, ?, ?)",
        (mid, len(vec), zeev._vec_pack(vec)),
    )
    con.commit()
    return mid


@pytest.fixture
def rag_db(zeev, tmp_path, monkeypatch):
    con = _fresh_rag_db(tmp_path)
    monkeypatch.setattr(zeev, "_db", lambda: con)
    return con


def test_recency_breaks_ties_between_equal_similarity(zeev, rag_db, monkeypatch):
    """Two equally-similar (but not near-duplicate) hits: the recent one
    should rank first. va/vb sit at the same angle from the query vector but
    are far enough apart from each other to survive the dedup pass."""
    query_v = [1.0, 0.0, 0.0]
    va = [0.866, 0.5, 0.0]
    vb = [0.866, -0.5, 0.0]
    assert zeev._cosine(query_v, va) == pytest.approx(zeev._cosine(query_v, vb), rel=1e-3)
    assert zeev._cosine(va, vb) < zeev._RAG_DEDUP_SIM

    now = datetime.now()
    _seed(zeev, rag_db, "old question about boston", (now - timedelta(days=200)).isoformat(), va, reply="old answer")
    _seed(zeev, rag_db, "new question about boston", now.isoformat(), vb, reply="new answer")
    monkeypatch.setattr(zeev, "embed_text", lambda *a, **kw: query_v)

    hits = zeev.retrieve_semantic("boston", k=2, min_sim=0.1)
    assert len(hits) == 2
    assert hits[0][1] == "new answer"


def test_min_sim_gates_on_raw_similarity_not_blended(zeev, rag_db, monkeypatch):
    """A very recent but weakly-similar hit must not sneak past min_sim just
    because the recency bonus pushed its blended score up."""
    query_v = [1.0, 0.0]
    weak_v = [0.0, 1.0]   # orthogonal -> sim == 0.0, recency bonus alone can't save it
    _seed(zeev, rag_db, "unrelated", datetime.now().isoformat(), weak_v, reply="unrelated reply")
    monkeypatch.setattr(zeev, "embed_text", lambda *a, **kw: query_v)

    assert zeev.retrieve_semantic("query", k=2, min_sim=0.5) == []


def test_dedup_skips_near_duplicate_vector(zeev, rag_db, monkeypatch):
    """Two near-identical hits (e.g. the same question asked twice) should
    not both consume the k budget -- the older/duplicate one is skipped so a
    third, differently-worded hit can surface instead."""
    now = datetime.now()
    v = [1.0, 0.0, 0.0]
    v_dup = [0.999, 0.001, 0.0001]     # cosine > _RAG_DEDUP_SIM to v
    v_other = [0.6, 0.8, 0.0]          # decently similar but a distinct topic

    assert zeev._cosine(v, v_dup) >= zeev._RAG_DEDUP_SIM
    assert zeev._cosine(v, v_other) < zeev._RAG_DEDUP_SIM

    _seed(zeev, rag_db, "what's the weather", now.isoformat(), v, reply="weather reply")
    _seed(zeev, rag_db, "what's the weather today", (now - timedelta(hours=1)).isoformat(), v_dup, reply="dup reply")
    _seed(zeev, rag_db, "totally different topic", (now - timedelta(hours=2)).isoformat(), v_other, reply="other reply")

    monkeypatch.setattr(zeev, "embed_text", lambda *a, **kw: v)
    hits = zeev.retrieve_semantic("weather", k=2, min_sim=0.1)

    replies = [a for _, a in hits]
    assert "dup reply" not in replies
    assert "weather reply" in replies
    assert "other reply" in replies


def test_retrieve_semantic_still_returns_k_when_no_duplicates(zeev, rag_db, monkeypatch):
    """Sanity check the dedup pass doesn't accidentally starve normal results."""
    now = datetime.now()
    v1, v2 = [1.0, 0.0], [0.8, 0.6]   # distinct enough to clear _RAG_DEDUP_SIM
    _seed(zeev, rag_db, "q1", now.isoformat(), v1, reply="a1")
    _seed(zeev, rag_db, "q2", now.isoformat(), v2, reply="a2")
    monkeypatch.setattr(zeev, "embed_text", lambda *a, **kw: v1)

    hits = zeev.retrieve_semantic("q", k=2, min_sim=0.1)
    assert len(hits) == 2


# ---------------------------------------------------------------------------
# retrieve_relevant (keyword fallback): same recency nudge
# ---------------------------------------------------------------------------

def test_keyword_recency_breaks_ties(zeev, monkeypatch):
    now = datetime.now()
    entries = [
        {"role": "user", "content": "tell me about boston weather", "ts": (now - timedelta(days=200)).isoformat()},
        {"role": "assistant", "content": "old boston reply", "ts": (now - timedelta(days=200)).isoformat()},
        {"role": "user", "content": "tell me about boston weather", "ts": now.isoformat()},
        {"role": "assistant", "content": "new boston reply", "ts": now.isoformat()},
    ]
    index = {}
    for i, e in enumerate(entries):
        for w in zeev._tokenize(e["content"]):
            index.setdefault(w, []).append(i)
    monkeypatch.setattr(zeev, "_HISTORY_ENTRIES", entries)
    monkeypatch.setattr(zeev, "_HISTORY_INDEX", index)

    hits = zeev.retrieve_relevant("boston weather", k=2, min_score=1)
    assert hits[0][1] == "new boston reply"


def test_keyword_min_score_gates_on_raw_score(zeev, monkeypatch):
    """A recent-but-weak keyword match must not pass min_score via the
    recency bonus alone."""
    now = datetime.now()
    entries = [
        {"role": "user", "content": "completely unrelated text here", "ts": now.isoformat()},
        {"role": "assistant", "content": "reply", "ts": now.isoformat()},
    ]
    index = {}
    for i, e in enumerate(entries):
        for w in zeev._tokenize(e["content"]):
            index.setdefault(w, []).append(i)
    monkeypatch.setattr(zeev, "_HISTORY_ENTRIES", entries)
    monkeypatch.setattr(zeev, "_HISTORY_INDEX", index)

    assert zeev.retrieve_relevant("boston weather forecast", k=2, min_score=1) == []
