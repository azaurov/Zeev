"""Tests for zeev/rag_probe.py — the RAG-faithfulness dashboard.

No real network/LLM calls here: `zeev._llm_complete` is always stubbed. The
DB tests use a fresh in-memory/temp-file sqlite connection swapped in via
`monkeypatch.setattr(zeev, "_db", lambda: con)`, the same pattern
test_semantic_memory.py and test_reminders.py already use.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "zeev"))
import rag_probe  # noqa: E402


# ---------------------------------------------------------------------------
# Grading — GROUNDED / UNGROUNDED / UNSURE parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected_grounded", [
    ("GROUNDED\nEverything checks out.", 1),
    ("UNGROUNDED\nThe answer invents a detail not in the passage.", 0),
    ("UNSURE\nCan't tell from the context given.", None),
    ("grounded\nlowercase still counts", 1),
    ("Ungrounded.\nWith trailing punctuation.", 0),
    ("**UNGROUNDED**\nmarkdown-bolded verdict", 0),
])
def test_parse_grade_verdicts(raw, expected_grounded):
    grounded, _note = rag_probe.parse_grade(raw)
    assert grounded == expected_grounded


def test_parse_grade_ungrounded_wins_over_substring_match():
    """"GROUNDED" is a literal substring of "UNGROUNDED" (starts at index 2).
    A naive `"GROUNDED" in text` check would misclassify every UNGROUNDED
    verdict as GROUNDED; this pins that UNGROUNDED is checked first."""
    grounded, _ = rag_probe.parse_grade("UNGROUNDED\nbecause X")
    assert grounded == 0


def test_grade_prompt_exempts_sarina_persona_naming():
    """The grader has no other way to know Sarina is a real persona (Zeev's
    spoken-word voice, see zeev.py's SYSTEM_PROMPT) -- live 2026-08-05, five
    separate probes were flagged UNGROUNDED purely because the answer said
    "Sarina:" where the retrieved context only said "Zeev:". The prompt must
    tell the grader these are the same speaker."""
    prompt = rag_probe._GRADE_PROMPT.format(context="Zeev: hello", answer="Sarina: hello")
    assert "Sarina" in prompt and "Zeev" in prompt
    assert "same speaker" in prompt.lower() or "two names" in prompt.lower()


def test_grade_prompt_instructs_checking_for_verbatim_quotes():
    """Live 2026-08-05: an answer that verbatim-matched a line in the context
    was still graded UNGROUNDED by the grader LLM -- a plain misjudgment, not
    a persona-naming false positive. The prompt now explicitly tells the
    grader to check for exact/near-exact quotes before flagging a claim."""
    prompt = rag_probe._GRADE_PROMPT.format(context="c", answer="a")
    assert "verbatim" in prompt.lower() or "quote" in prompt.lower()


def test_parse_grade_note_is_second_line():
    _grounded, note = rag_probe.parse_grade("GROUNDED\nThe reply only restates the passage.")
    assert note == "The reply only restates the passage."


def test_parse_grade_no_note_line():
    grounded, note = rag_probe.parse_grade("GROUNDED")
    assert grounded == 1
    assert note == ""


def test_parse_grade_unparseable():
    grounded, note = rag_probe.parse_grade("I cannot determine this.")
    assert grounded is None
    assert "unparseable" in note


def test_parse_grade_empty():
    grounded, note = rag_probe.parse_grade("")
    assert grounded is None
    assert "empty" in note


def test_parse_grade_verdict_not_on_first_line_still_found():
    """Model ignored the "first line" instruction -- still scan the whole
    reply, UNGROUNDED-first, rather than giving up."""
    grounded, _ = rag_probe.parse_grade("Let me think about this.\nMy verdict: UNGROUNDED.")
    assert grounded == 0


# ---------------------------------------------------------------------------
# Question-generation prompt construction
# ---------------------------------------------------------------------------

def test_torah_question_prompt_contains_the_excerpt():
    prompt = rag_probe._TORAH_QUESTION_PROMPT.format(excerpt="Blessed are You, Adonoy our God.")
    assert "Blessed are You, Adonoy our God." in prompt


def test_torah_question_prompt_has_no_ref_placeholder():
    """The generator must never be able to leak a ref it was never given --
    verified structurally: the template's only format placeholder is
    {excerpt}, so _gen_torah_question() has no way to interpolate a ref even
    by accident (it only ever passes `excerpt=`)."""
    import string
    fields = [name for _, name, _, _ in string.Formatter().parse(rag_probe._TORAH_QUESTION_PROMPT)
              if name is not None]
    assert fields == ["excerpt"]


def test_gen_torah_question_never_sends_the_ref(monkeypatch):
    captured = {}

    def fake_llm_complete(msgs, model, max_tokens=300, json_mode=False):
        captured["prompt"] = msgs[0]["content"]
        return "What does this passage teach?", None

    class FakeZeev:
        MODELS = {"1": ("fast-model", "fast")}
        _llm_complete = staticmethod(fake_llm_complete)

    question, err = rag_probe._gen_torah_question(FakeZeev, "Peace be upon you, ministering angels.")
    assert err is None
    assert question == "What does this passage teach?"
    assert "Shalom Aleichem" not in captured["prompt"]  # the (hypothetical) ref was never passed in
    assert "Peace be upon you, ministering angels." in captured["prompt"]


def test_gen_torah_question_propagates_llm_error():
    class FakeZeev:
        MODELS = {"1": ("fast-model", "fast")}
        _llm_complete = staticmethod(lambda *a, **kw: (None, "rate-limited"))

    question, err = rag_probe._gen_torah_question(FakeZeev, "some text")
    assert question is None
    assert err == "rate-limited"


# ---------------------------------------------------------------------------
# Noise filtering for sampled history questions
# ---------------------------------------------------------------------------

class _FakeZeevNoise:
    @staticmethod
    def _is_whisper_hallucination(text):
        return text.strip().lower() in {"thank you.", "thanks for watching"}


@pytest.mark.parametrize("text,expected", [
    ("What time does the pharmacy close on Sundays?", True),
    ("hi", False),                       # too short
    ("ok", False),                       # too short
    ("Thank you.", False),                # known hallucination
    ("               ", False),           # no real words
    ("!!!???...", False),                 # no \w{3,} run
    ("Remind me to call my mom at 5pm tomorrow please", True),
])
def test_looks_like_real_question(text, expected):
    assert rag_probe._looks_like_real_question(_FakeZeevNoise, text) == expected


# ---------------------------------------------------------------------------
# DB schema / insert round-trip
# ---------------------------------------------------------------------------

def _fresh_zeev_db(tmp_path):
    con = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def test_ensure_probes_table_is_idempotent(zeev, tmp_path, monkeypatch):
    con = _fresh_zeev_db(tmp_path)
    monkeypatch.setattr(zeev, "_db", lambda: con)
    rag_probe._ensure_probes_table(zeev)
    rag_probe._ensure_probes_table(zeev)  # must not raise on a second call
    cols = {r["name"] for r in con.execute("PRAGMA table_info(rag_probes)")}
    assert cols == {"id", "ts", "source", "question", "retrieved_ref",
                     "retrieved_text", "answer", "grounded", "grader_note"}


def test_save_and_read_probe_round_trip(zeev, tmp_path, monkeypatch):
    con = _fresh_zeev_db(tmp_path)
    monkeypatch.setattr(zeev, "_db", lambda: con)
    rag_probe._ensure_probes_table(zeev)

    rag_probe._save_probe(
        zeev, source="torah", question="What happened at the start?",
        retrieved_ref="Genesis 1", retrieved_text="In the beginning...",
        answer="God created the heavens and the earth.",
        grounded=1, grader_note="matches the passage",
    )
    row = con.execute("SELECT * FROM rag_probes").fetchone()
    assert row["source"] == "torah"
    assert row["question"] == "What happened at the start?"
    assert row["retrieved_ref"] == "Genesis 1"
    assert row["grounded"] == 1
    assert row["grader_note"] == "matches the passage"
    assert isinstance(row["ts"], float)


def test_save_probe_grounded_can_be_null(zeev, tmp_path, monkeypatch):
    """grounded=NULL means "grader couldn't judge" -- must round-trip as
    None, not 0, or an UNSURE probe would silently count as UNGROUNDED."""
    con = _fresh_zeev_db(tmp_path)
    monkeypatch.setattr(zeev, "_db", lambda: con)
    rag_probe._ensure_probes_table(zeev)

    rag_probe._save_probe(
        zeev, source="history", question="q", retrieved_ref="1",
        retrieved_text="ctx", answer="a", grounded=None, grader_note="unsure",
    )
    row = con.execute("SELECT grounded FROM rag_probes").fetchone()
    assert row["grounded"] is None


# ---------------------------------------------------------------------------
# Retrieval-block extraction from the assembled system prompt
# ---------------------------------------------------------------------------

def test_torah_block_extraction_stops_before_next_section():
    sys_prompt = (
        "You are Zeev.\n\n"
        "## Right now: Wednesday\n\n"
        "## Relevant Torah/Talmud passages:\n"
        "Genesis 1: In the beginning God created the heaven and the earth.\n\n"
        "Reply in English only."
    )

    class FakeZeev:
        @staticmethod
        def torah_search(q, k=3):
            return [("Genesis 1", "In the beginning God created the heaven and the earth.", "")]

    ref, text = rag_probe._torah_retrieval(FakeZeev, "what happened at the start", sys_prompt)
    assert ref == "Genesis 1"
    assert text == "Genesis 1: In the beginning God created the heaven and the earth."
    assert "Reply in English" not in text


def test_torah_block_extraction_multi_ref():
    sys_prompt = (
        "## Relevant Torah/Talmud passages:\n"
        "Genesis 1: In the beginning...\n"
        "Exodus 3: And Moses said unto God, Who am I.\n\n"
        "## Instruction: recite verbatim"
    )

    class FakeZeev:
        @staticmethod
        def torah_search(q, k=3):
            return [("Genesis 1", "...", ""), ("Exodus 3", "...", "")]

    ref, text = rag_probe._torah_retrieval(FakeZeev, "q", sys_prompt)
    assert ref == "Genesis 1, Exodus 3"
    assert "Exodus 3: And Moses said unto God, Who am I." in text
    assert "Instruction" not in text


def test_torah_block_extraction_absent_returns_empty():
    class FakeZeev:
        @staticmethod
        def torah_search(q, k=3):
            return []

    ref, text = rag_probe._torah_retrieval(FakeZeev, "unrelated question", "no torah section here")
    assert ref == "" and text == ""


def test_history_block_extraction_stops_before_language_guard_note():
    sys_prompt = (
        "## Relevant past exchanges:\n"
        "User: what is up\nZeev: שלום עום\n\n"
        "Note: the past exchanges above may be in another language."
    )

    class FakeZeev:
        _db_lock = __import__("threading").Lock()

        @staticmethod
        def retrieve_semantic(q, **kw):
            return [("what is up", "שלום עום")]

        @staticmethod
        def retrieve_relevant(q, **kw):
            return []

        @staticmethod
        def _db():
            con = sqlite3.connect(":memory:")
            con.row_factory = sqlite3.Row
            con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT)")
            con.execute("INSERT INTO messages VALUES (42, 'user', 'what is up')")
            con.commit()
            return con

    ref, text = rag_probe._history_retrieval(FakeZeev, "q", sys_prompt)
    assert ref == "42"
    assert "Note: the past exchanges" not in text
    assert text == "User: what is up\nZeev: שלום עום"


def test_history_retrieval_merges_concurrently_triggered_torah_block():
    """A question can land a history hit AND trip needs_torah() in the real
    prompt (e.g. "help with the bedtime angel prayer") -- the answer may be
    grounded in the Torah block instead of the history block. Live 2026-08-05:
    grading only the history slice flagged a correct, Torah-sourced answer as
    UNGROUNDED."""
    sys_prompt = (
        "## Relevant past exchanges:\n"
        "User: what is up\nZeev: not much\n\n"
        "## Relevant Torah/Talmud passages:\n"
        "Psalms 91: He who dwells in the shelter of the Most High.\n\n"
        "Reply in English only."
    )

    class FakeZeev:
        _db_lock = __import__("threading").Lock()

        @staticmethod
        def retrieve_semantic(q, **kw):
            return [("what is up", "not much")]

        @staticmethod
        def retrieve_relevant(q, **kw):
            return []

        @staticmethod
        def torah_search(q, k=3):
            return [("Psalms 91", "He who dwells in the shelter of the Most High.", "")]

        @staticmethod
        def _db():
            con = sqlite3.connect(":memory:")
            con.row_factory = sqlite3.Row
            con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT)")
            con.execute("INSERT INTO messages VALUES (7, 'user', 'what is up')")
            con.commit()
            return con

    ref, text = rag_probe._history_retrieval(FakeZeev, "q", sys_prompt)
    assert ref == "7, Psalms 91"
    assert "not much" in text
    assert "He who dwells in the shelter of the Most High." in text


def test_history_retrieval_merges_concurrently_injected_location_block():
    """The ambient location block is injected on every turn regardless of
    retrieval (zeev.py's _build_system_prompt), so a question like "what's
    your coordinates" can be legitimately grounded in it rather than history
    RAG. Live 2026-08-06: a correct answer citing the real ambient location
    ("Canton, Massachusetts") got graded UNGROUNDED because this function
    never showed the grader that block."""
    sys_prompt = (
        "## Relevant past exchanges:\n"
        "User: what is up\nZeev: not much\n\n"
        "## Approximate location: Canton, Massachusetts, United States\n"
        "This is ambient context. Do not mention it unless the user asks "
        "where they are, or it is needed to answer (weather, what's nearby).\n\n"
        "Reply in English only."
    )

    class FakeZeev:
        _db_lock = __import__("threading").Lock()

        @staticmethod
        def retrieve_semantic(q, **kw):
            return [("what is up", "not much")]

        @staticmethod
        def retrieve_relevant(q, **kw):
            return []

        @staticmethod
        def torah_search(q, k=3):
            return []

        @staticmethod
        def _db():
            con = sqlite3.connect(":memory:")
            con.row_factory = sqlite3.Row
            con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT)")
            con.execute("INSERT INTO messages VALUES (7, 'user', 'what is up')")
            con.commit()
            return con

    ref, text = rag_probe._history_retrieval(FakeZeev, "q", sys_prompt)
    assert ref == "7"  # location has no ref id, only text
    assert "not much" in text
    assert "Canton, Massachusetts" in text
    assert "Ambient location (real-time, not retrieved history):" in text


def test_history_retrieval_no_torah_block_unaffected():
    """No concurrent Torah block -- output must match the pre-merge behavior
    exactly (empty torah ref/text contributes nothing)."""
    sys_prompt = "## Relevant past exchanges:\nUser: hi\nZeev: hello\n\nReply in English only."

    class FakeZeev:
        _db_lock = __import__("threading").Lock()

        @staticmethod
        def retrieve_semantic(q, **kw):
            return [("hi", "hello")]

        @staticmethod
        def retrieve_relevant(q, **kw):
            return []

        @staticmethod
        def torah_search(q, k=3):
            return []

        @staticmethod
        def _db():
            con = sqlite3.connect(":memory:")
            con.row_factory = sqlite3.Row
            con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT)")
            con.execute("INSERT INTO messages VALUES (1, 'user', 'hi')")
            con.commit()
            return con

    ref, text = rag_probe._history_retrieval(FakeZeev, "q", sys_prompt)
    assert ref == "1"
    assert text == "User: hi\nZeev: hello"


# ---------------------------------------------------------------------------
# Report stats (pure)
# ---------------------------------------------------------------------------

def test_grounded_rate_by_source():
    rows = [
        ("torah", 1), ("torah", 1), ("torah", 0),
        ("history", 1), ("history", None),
    ]
    stats = rag_probe._grounded_rate_by_source(rows)
    assert stats["torah"] == {"total": 3, "grounded": 2, "ungrounded": 1, "unsure": 0}
    assert stats["history"] == {"total": 2, "grounded": 1, "ungrounded": 0, "unsure": 1}


def test_grounded_rate_by_source_empty():
    assert rag_probe._grounded_rate_by_source([]) == {}


# ---------------------------------------------------------------------------
# Full torah/history probe flow, LLM stubbed
# ---------------------------------------------------------------------------

def test_torah_probe_rerolls_when_generated_question_gets_no_retrieval(zeev, tmp_path, monkeypatch):
    """The generated question can fail to trip needs_torah's gate (or
    torah_search can come up empty) -- that probe attempt must reroll rather
    than logging a row with nothing to grade against."""
    con = _fresh_zeev_db(tmp_path)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT, ts TEXT)")
    con.commit()
    monkeypatch.setattr(zeev, "_db", lambda: con)

    torah_db = tmp_path / "torah.db"
    tcon = sqlite3.connect(torah_db)
    tcon.execute("CREATE TABLE passages (source TEXT, ref TEXT, en TEXT, he TEXT)")
    tcon.execute("INSERT INTO passages VALUES ('Tanakh', 'Genesis 1', ?, '')",
                 ("In the beginning God created the heaven and the earth. " * 5,))
    tcon.commit()
    tcon.close()
    monkeypatch.setattr(zeev, "TORAH_DB", torah_db)

    # Question generator produces ordinary text with none of _TORAH_RE's
    # vocabulary, so needs_torah() never fires and no retrieval happens.
    monkeypatch.setattr(zeev, "_llm_complete",
                         lambda msgs, model, max_tokens=300, json_mode=False:
                             ("what happened first", None))

    result = rag_probe._run_torah_probe(zeev, verbose=False, max_attempts=2)
    assert result is None


def test_torah_probe_rerolls_when_retrieval_misses_seed_passage(zeev, tmp_path, monkeypatch):
    """The generated question is seeded from one passage, but at grading time
    torah_search(question) is a SEPARATE, independent FTS5 call that can
    return an entirely different passage. Live 2026-08-05: this produced an
    honest 'the passage doesn't mention that' answer that got graded as a
    hallucination, because it was graded against unrelated retrieved content
    instead of the seed passage. Must reroll, not log, when retrieval misses
    the seed ref."""
    con = _fresh_zeev_db(tmp_path)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT, ts TEXT)")
    con.commit()
    monkeypatch.setattr(zeev, "_db", lambda: con)

    torah_db = tmp_path / "torah.db"
    tcon = sqlite3.connect(torah_db)
    tcon.execute("CREATE TABLE passages (source TEXT, ref TEXT, en TEXT, he TEXT)")
    tcon.execute("INSERT INTO passages VALUES ('Tanakh', 'Genesis 1', ?, '')",
                 ("In the beginning God created the heaven and the earth. " * 5,))
    tcon.commit()
    tcon.close()
    monkeypatch.setattr(zeev, "TORAH_DB", torah_db)

    # Question generator emits Torah-vocabulary text so needs_torah() fires,
    # but torah_search is stubbed to return an UNRELATED ref -- simulating a
    # retrieval miss on the seed passage (Genesis 1).
    monkeypatch.setattr(zeev, "_llm_complete",
                         lambda msgs, model, max_tokens=300, json_mode=False:
                             ("What does the Torah say about this passage?", None))
    monkeypatch.setattr(zeev, "torah_search",
                         lambda query, k=3: [("Exodus 3", "And Moses said unto God, Who am I.", "")])

    result = rag_probe._run_torah_probe(zeev, verbose=False, max_attempts=2)
    assert result is None


def test_history_probe_skips_cleanly_with_no_messages(zeev, tmp_path, monkeypatch):
    con = _fresh_zeev_db(tmp_path)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT, ts TEXT)")
    con.commit()
    monkeypatch.setattr(zeev, "_db", lambda: con)

    result = rag_probe._run_history_probe(zeev, verbose=False)
    assert result is None
