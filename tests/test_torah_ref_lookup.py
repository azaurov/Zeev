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
        # Yihyu L'ratzon. Sefaria has no English for the Shabbat rows, so their
        # `en` column carries the pointed HEBREW while the Weekday row carries
        # the English -- reproduced here because the alias probes both to give
        # the model the real gloss AND the real Hebrew.
        ("Siddur", "Siddur Ashkenaz, Weekday, Shacharit, Amidah, Concluding Passage",
         "May the words of my mouth and the thoughts of my heart be acceptable "
         "before You, Adonoy, my Rock and my Redeemer."),
        ("Siddur", "Siddur Ashkenaz, Shabbat, Shacharit, Amidah, Concluding Passage",
         "יִהְיוּ לְרָצון אִמְרֵי פִי וְהֶגְיון לִבִּי לְפָנֶיךָ. ה' צוּרִי וְגואֲלִי"),
        # The bedtime service. Note the spelling the ref actually uses:
        # apostrophe in "Keri'at", and "Hamita" with NO trailing 'h' -- probes
        # for "Kriat"/"HaMitah" find nothing and it reads as absent when it is
        # not. That mis-spelling is why an earlier pass concluded it was
        # missing and pointed "angelic prayer" at the wrong passage.
        ("Siddur", "Siddur Ashkenaz, Weekday, Maariv, Keri'at Shema al Hamita",
         "I hereby forgive anyone who has angered me, or sinned against me."),
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


# ---------------------------------------------------------------------------
# Alias: "the angelic prayer" -> Yihyu L'ratzon
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "recite yihyu l'ratzon",
    "what is Yihyu Lratzon",
])
def test_angelic_prayer_resolves_to_yihyu_lratzon(zeev, torah_db, query):
    """Alex's name for the meditation closing the Amidah (Psalms 19:15).

    Confirmed with him 2026-07-31; the other candidate was Shalom Aleichem,
    which greets the ministering angels and is findable by its own name.
    """
    hits = zeev.torah_search(query)
    refs = [r for r, _ in hits]
    assert any("Amidah, Concluding Passage" in r for r in refs), refs


def test_alias_supplies_both_english_and_pointed_hebrew(zeev, torah_db):
    """Probing only the Weekday row leaves the model inventing the Hebrew,
    which is the exact failure the alias exists to stop."""
    bodies = [en for _, en in zeev.torah_search("recite yihyu l'ratzon")]
    assert any("May the words of my mouth" in b for b in bodies), bodies
    assert any("יִהְיוּ לְרָצון" in b for b in bodies), bodies


def test_alias_hit_returns_alone(zeev, torah_db):
    """An alias names the exact passage, so nothing is topped up behind it.

    Live against the real DB this pulled in Zohar Shemot 45, which merely
    shares vocabulary -- and unrelated context is what the model paraphrases
    when it drifts. The exclusivity has to survive BOTH _torah_ref_lookup and
    torah_search's own FTS top-up; fixing only the former left it in.
    """
    hits = zeev.torah_search("recite yihyu l'ratzon")
    assert all("Amidah, Concluding Passage" in r for r, _ in hits), hits


def test_alias_does_not_fire_on_ordinary_speech(zeev, torah_db):
    """The gate is what keeps "an angelic voice" out; this pins that the alias
    itself needs the prayer sense too, not merely the word."""
    assert not zeev._YIHYU_ALIAS_RE.search("she has an angelic voice")
    assert not zeev._ANGEL_PRAYER_RE.search("she has an angelic voice")


@pytest.mark.parametrize("query", [
    "Help me with the angelic prayer, the bedtime angelic prayer.",
    "the bedtime shema",
    "what do I say before bed",
    "keriat shema al hamita",
])
def test_bedtime_wins_over_the_ambiguous_angelic_alias(zeev, torah_db, query):
    """Live 2026-07-31: Alex asked for "the angelic prayer, the BEDTIME angelic
    prayer" and the alias injected Yihyu L'ratzon instead. The 70B ignored the
    wrong passage and answered correctly from its own knowledge, which is luck,
    not a working retrieval.

    "bedtime angelic prayer" matches both aliases, so first-match-wins ordering
    is what makes the explicit word decide.
    """
    hits = zeev.torah_search(query)
    assert hits, f"{query!r} found nothing"
    assert all("Shema al Hamita" in r for r, _ in hits), hits


def test_bare_angelic_prayer_offers_both_candidates(zeev, torah_db):
    """Still ambiguous, so it supplies both rather than guessing a third time:
    picking one unilaterally is what produced the wrong answer."""
    refs = [r for r, _ in zeev.torah_search("help me with the angelic prayer")]
    assert any("Shema al Hamita" in r for r in refs), refs
    assert any("Amidah, Concluding Passage" in r for r in refs), refs
