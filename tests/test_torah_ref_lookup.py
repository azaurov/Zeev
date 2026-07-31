"""Finding a prayer by its NAME rather than by its body text.

data/torah.db declares `ref` UNINDEXED in the FTS5 schema -- only `en` is
searchable -- so any passage whose title does not also appear inside its own
English translation is invisible to `MATCH`, however it is spelled. That is
most of the siddur.

Live 2026-07-31, "help me with the angelic prayer" produced an invented
English text and an invented Hebrew one, naming a prayer ("Tefillat HaShalom")
that does not exist. Shalom Aleichem -- the Friday-night hymn addressed to the
ministering angels -- was in the database the whole time and unreachable.

The fixture mirrors the production schema exactly, including UNINDEXED, since
that single keyword is the entire reason this code path exists. A test built on
an indexed `ref` would pass without the fix.
"""
import sqlite3

import pytest


@pytest.fixture
def torah_db(tmp_path, zeev, monkeypatch):
    path = tmp_path / "torah.db"
    con = sqlite3.connect(path)
    con.execute("""
        CREATE VIRTUAL TABLE passages USING fts5(
            source   UNINDEXED,
            ref      UNINDEXED,
            en,
            he       UNINDEXED,
            tokenize = 'unicode61'
        )
    """)
    rows = [
        # (source, ref, en) -- note none of the English bodies contain the
        # prayer's own name, which is exactly the production situation.
        ("Siddur", "Siddur Ashkenaz, Shabbat, Shabbat Evening, Shalom Aleichem",
         "Peace be upon you, ministering angels, messengers of the Most High."),
        ("Siddur", "Siddur Ashkenaz, Weekday, Shacharit, Preparatory Prayers, Ma Tovu",
         "How goodly are your tents, O Jacob, your dwelling places, O Israel."),
        ("Siddur", "Siddur Ashkenaz, Weekday, Shacharit, Preparatory Prayers, Asher Yatzar",
         "Blessed are You who formed the human being with wisdom."),
        ("Siddur", "Siddur Ashkenaz, Shabbat, Havdalah",
         "Blessed are You who separates between the holy and the profane."),
        ("Tanakh", "Genesis 1",
         "In the beginning God created the heaven and the earth."),
    ]
    con.executemany(
        "INSERT INTO passages (source, ref, en, he) VALUES (?, ?, ?, '')", rows)
    con.commit()
    con.close()
    monkeypatch.setattr(zeev, "TORAH_DB", path)
    return path


def test_ref_is_unindexed_so_match_alone_cannot_find_these(torah_db):
    """Pins the premise. If this ever starts passing, `ref` became indexed and
    the whole ref-lookup path can be deleted."""
    con = sqlite3.connect(torah_db)
    for name in ("Shalom Aleichem", "Ma Tovu", "Asher Yatzar"):
        hits = con.execute(
            "SELECT ref FROM passages WHERE passages MATCH ?", (name,)).fetchall()
        assert hits == [], f"{name} unexpectedly reachable via MATCH"
    con.close()


@pytest.mark.parametrize("query,expected", [
    ("say Shalom Aleichem", "Shalom Aleichem"),
    ("what is Ma Tovu", "Ma Tovu"),
    ("teach me Asher Yatzar", "Asher Yatzar"),
    ("recite Havdalah", "Havdalah"),
])
def test_prayers_are_found_by_name(zeev, torah_db, query, expected):
    hits = zeev.torah_search(query)
    assert hits, f"{query!r} found nothing"
    assert expected in hits[0][0], f"{query!r} -> {hits[0][0]}"


def test_two_char_words_still_form_a_bigram(zeev, torah_db):
    """"Ma Tovu" is why the tokenizer takes 2-char words. At a 3-char floor
    "ma" is dropped, one word remains, no bigram is formed and the lookup
    returns nothing at all."""
    assert any("Ma Tovu" in ref for ref, _ in zeev.torah_search("what is ma tovu"))


def test_body_text_search_still_works(zeev, torah_db):
    """The ref lookup leads but must not displace ordinary FTS retrieval."""
    hits = zeev.torah_search("who created the heaven and the earth")
    assert any("Genesis" in ref for ref, _ in hits), hits


def test_generic_words_do_not_hijack_the_lookup(zeev, torah_db):
    """"prayer"/"hebrew"/"blessing" are in _TORAH_REF_SKIP: as LIKE probes they
    match huge swathes of the siddur and would crowd out the real hit."""
    for w in ("prayer", "blessing", "hebrew", "say", "recite"):
        assert w in zeev._TORAH_REF_SKIP, w
