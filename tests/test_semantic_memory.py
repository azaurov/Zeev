"""Tests for the semantic-memory layer added in Phase 5.

Retrieval quality itself needs a live bosgame, so these cover the parts that
must hold regardless: the vector round-trip, cosine behavior, that everything
degrades to the keyword index when embedding is unavailable, and that
save_memory can no longer lose the whole fact table halfway through.
"""
import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Vector pack / unpack / cosine
# ---------------------------------------------------------------------------

def test_vector_roundtrip(zeev):
    vec = [0.5, -0.25, 1e-3, 12345.0, -0.0]
    blob = zeev._vec_pack(vec)
    out = zeev._vec_unpack(blob, len(vec))
    assert len(out) == len(vec)
    for a, b in zip(vec, out):
        assert a == pytest.approx(b, rel=1e-6, abs=1e-9)


def test_vector_blob_is_four_bytes_per_dim(zeev):
    """dim is stored alongside the blob; they must agree or reads misalign."""
    assert len(zeev._vec_pack([1.0] * 768)) == 768 * 4


def test_cosine_identical_is_one(zeev):
    v = [0.1, 0.2, 0.3, -0.4]
    assert zeev._cosine(v, v) == pytest.approx(1.0, abs=1e-9)


def test_cosine_orthogonal_is_zero(zeev):
    assert zeev._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-9)


def test_cosine_opposite_is_minus_one(zeev):
    assert zeev._cosine([1.0, 2.0], [-1.0, -2.0]) == pytest.approx(-1.0, abs=1e-9)


def test_cosine_zero_vector_does_not_divide_by_zero(zeev):
    """An all-zero embedding is a plausible degenerate response; must not raise."""
    assert zeev._cosine([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_cosine_ignores_magnitude(zeev):
    a, b = [1.0, 2.0, 3.0], [10.0, 20.0, 30.0]
    assert zeev._cosine(a, b) == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

def test_embed_returns_none_when_bosgame_unset(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "BOSGAME_URL", "")
    assert zeev.embed_text("anything") is None


def test_embed_returns_none_for_empty_text(zeev):
    assert zeev.embed_text("") is None
    assert zeev.embed_text("   ") is None


def test_embed_returns_none_on_transport_error(zeev, monkeypatch):
    """Offline is a normal state for a travelling Pi, not an exception."""
    monkeypatch.setattr(zeev, "BOSGAME_URL", "https://example.invalid/ollama")

    def _boom(*a, **kw):
        raise OSError("no route to host")

    monkeypatch.setattr(zeev.requests, "post", _boom)
    assert zeev.embed_text("hello") is None


def test_retrieve_semantic_empty_without_embeddings(zeev, monkeypatch):
    """Must return [] (so the caller falls back), never raise."""
    monkeypatch.setattr(zeev, "embed_text", lambda *a, **kw: None)
    assert zeev.retrieve_semantic("what did we say about boston") == []


def test_index_message_vec_noop_without_embeddings(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "embed_text", lambda *a, **kw: None)
    assert zeev.index_message_vec(1, "some content") is False


# ---------------------------------------------------------------------------
# save_memory atomicity
# ---------------------------------------------------------------------------

def _fresh_db(tmp_path):
    con = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "fact TEXT NOT NULL UNIQUE)")
    con.commit()
    return con


def test_save_memory_replaces_facts(zeev, tmp_path, monkeypatch):
    con = _fresh_db(tmp_path)
    monkeypatch.setattr(zeev, "_db", lambda: con)
    zeev.save_memory(["Alex lives in Boston", "Alex likes jazz"])
    zeev.save_memory(["Alex moved to Denver"])
    got = [r["fact"] for r in con.execute("SELECT fact FROM facts ORDER BY id")]
    assert got == ["Alex moved to Denver"]


def test_save_memory_rolls_back_on_failure(zeev, tmp_path, monkeypatch):
    """The bug this replaced: DELETE then INSERT outside a transaction, so a
    failure between them wiped every fact the user had ever taught Zeev."""
    con = _fresh_db(tmp_path)
    monkeypatch.setattr(zeev, "_db", lambda: con)
    zeev.save_memory(["keep me", "and me"])

    class Boom(Exception):
        pass

    class FailingInsert:
        """sqlite3.Connection attributes are read-only, so proxy instead."""
        def __init__(self, real):
            self._real = real

        def executemany(self, *a, **kw):
            raise Boom("write failed mid-swap")

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(zeev, "_db", lambda: FailingInsert(con))
    with pytest.raises(Boom):
        zeev.save_memory(["replacement"])
    monkeypatch.setattr(zeev, "_db", lambda: con)

    survived = {r["fact"] for r in con.execute("SELECT fact FROM facts")}
    assert survived == {"keep me", "and me"}, "facts were lost on a failed write"


def test_save_memory_tolerates_duplicates(zeev, tmp_path, monkeypatch):
    """facts.fact is UNIQUE; extract_memory can hand back repeats."""
    con = _fresh_db(tmp_path)
    monkeypatch.setattr(zeev, "_db", lambda: con)
    zeev.save_memory(["dup", "dup", "other"])
    got = sorted(r["fact"] for r in con.execute("SELECT fact FROM facts"))
    assert got == ["dup", "other"]
