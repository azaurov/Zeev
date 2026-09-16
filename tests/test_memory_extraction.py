"""Tests for extract_memory()'s prompt scoping and dedup fix.

Found live 2026-08-06 investigating a rag_probe.py finding pattern: Zeev
would confidently invent details about Alex's nieces ("living in New
Rochelle", "flying to London") in one reply, and the SYSTEM_PROMPT
clarification-instruction mitigation (see docs/rag-probe-findings.md) looked
only partially effective. The real reason turned out not to be the model
re-hallucinating fresh each time -- extract_memory() runs automatically every
5 turns in device mode, reads the transcript (both USER and ZEEV lines) with
no check on who actually said what, and had written Zeev's own invented
details straight into USER_FACTS as established fact. Once there, they're
injected into every future system prompt unconditionally, so Zeev was
correctly *recalling* a poisoned memory, not inventing anything fresh.

Two fixes: (1) the extraction prompt now explicitly scopes to what the USER
said, not what ZEEV asserted; (2) the merge/dedup check, previously an exact
string match, is normalized so "Alex's X" and "The user's X" collapse to the
same key -- the old check let re-extraction re-add the same fact under a
reworded subject every time (91 stored rows, ~35-40 actually unique).
"""
import json
from unittest.mock import MagicMock, patch


def test_prompt_scopes_extraction_to_user_statements(zeev):
    """The extraction prompt must explicitly tell the model not to trust
    Zeev's own assertions as user facts -- this is the actual fix, not just
    documentation, so pin the instruction text itself."""
    captured = {}

    def fake_feiergente(msgs, **kw):
        captured["prompt"] = msgs[-1]["content"]
        return None, "not configured"

    with patch.object(zeev, "_feiergente_complete", fake_feiergente), \
         patch.object(zeev, "_bosgame_complete", return_value=(None, "no bosgame")), \
         patch.object(zeev, "_llm_complete", return_value=(json.dumps({"facts": []}), None)):
        zeev.extract_memory([
            {"role": "user", "content": "I live in Boston"},
            {"role": "assistant", "content": "Nice!"},
        ])

    prompt = captured["prompt"]
    assert "ONLY from what the USER themselves said" in prompt
    assert "Zeev's own replies are not a reliable source" in prompt


def test_prompt_excludes_temporary_states(zeev):
    """Found live 2026-08-07: `facts` has no timestamp column, so a genuinely
    real but temporary state ("visiting Uncle Sasha for the summer... leaving
    today", from 2026-07-12) was still sitting in USER_FACTS a month later
    with no staleness signal, and got read back as if still current when
    Zeev was asked to clarify an unrelated prior reply -- inventing "Uncle
    Sasha and the summer visitors... heading back home today" out of nothing.
    The extraction prompt must steer away from storing one-time/temporary
    states as if they were durable facts in the first place, since there's
    nowhere to record when they stop being true."""
    captured = {}

    def fake_feiergente(msgs, **kw):
        captured["prompt"] = msgs[-1]["content"]
        return None, "not configured"

    with patch.object(zeev, "_feiergente_complete", fake_feiergente), \
         patch.object(zeev, "_bosgame_complete", return_value=(None, "no bosgame")), \
         patch.object(zeev, "_llm_complete", return_value=(json.dumps({"facts": []}), None)):
        zeev.extract_memory([
            {"role": "user", "content": "I'm visiting my uncle this weekend"},
            {"role": "assistant", "content": "Have a great time!"},
        ])

    prompt = captured["prompt"]
    assert "DURABLE facts" in prompt
    assert "no timestamps" in prompt


def test_fact_key_normalizes_subject_phrasing(zeev):
    """The actual dedup bug: 'Alex's nieces live in New Rochelle' and 'The
    user's nieces live in New Rochelle' must collapse to the same key."""
    assert zeev._fact_key("Alex's nieces live in New Rochelle.") == \
        zeev._fact_key("The user's nieces live in New Rochelle")
    assert zeev._fact_key("Alex enjoys jazz") == zeev._fact_key("The user enjoys jazz.")
    # Different facts must NOT collapse to the same key.
    assert zeev._fact_key("Alex enjoys jazz") != zeev._fact_key("Alex enjoys sushi")


def test_extract_memory_rejects_reworded_duplicate(zeev, monkeypatch):
    """End-to-end: an LLM response that re-asserts an existing fact under a
    different subject phrasing must not be appended as a 'new' fact."""
    monkeypatch.setattr(zeev, "USER_FACTS", ["Alex enjoys jazz music."])
    monkeypatch.setattr(zeev, "save_memory", lambda facts: None)

    with patch.object(zeev, "_feiergente_complete",
                       return_value=(json.dumps({"facts": ["The user enjoys jazz music"]}), None)):
        result = zeev.extract_memory([
            {"role": "user", "content": "yeah I love jazz"},
            {"role": "assistant", "content": "Great taste!"},
        ])

    assert result == ["Alex enjoys jazz music."]  # unchanged, no reworded duplicate added


def test_extract_memory_still_accepts_genuinely_new_facts(zeev, monkeypatch):
    """The dedup fix must not become so aggressive it blocks real new facts."""
    monkeypatch.setattr(zeev, "USER_FACTS", ["Alex enjoys jazz music."])
    monkeypatch.setattr(zeev, "save_memory", lambda facts: None)

    with patch.object(zeev, "_feiergente_complete",
                       return_value=(json.dumps({"facts": ["Alex has a dog named Leo"]}), None)):
        result = zeev.extract_memory([
            {"role": "user", "content": "my dog Leo is great"},
            {"role": "assistant", "content": "Sounds like a good boy!"},
        ])

    assert result == ["Alex enjoys jazz music.", "Alex has a dog named Leo"]


# ---------------------------------------------------------------------------
# Transient-fact filter (2026-09-16)
#
# The two guards above are prompt-only, and the small models doing the
# extraction (qwen2.5 -> llama3.2:1b -> gpt-oss-20b) don't reliably obey
# either. Found live: `facts` held "The user is going back home today in the
# car." -- near-verbatim from Zeev's OWN reply, so it broke the who-said-it
# guard AND the no-temporary-states guard at once -- plus "Alex is going to
# drive safely", extracted from Zeev's own sign-off. Because facts are
# injected into every system prompt, this self-reinforced into a six-week
# verbal tic: Zeev wished Alex safe travels on turns with no journey in them.
# _is_transient_fact is the code-level backstop the prompt guard needed.
# ---------------------------------------------------------------------------

def test_transient_filter_rejects_the_real_incident_facts(zeev):
    """The three rows actually found in the live facts table on 2026-09-16."""
    for f in ("The user is going back home today in the car.",
              "Alex is going to drive safely",
              "Alex is going home in the car"):
        assert zeev._is_transient_fact(f), f


def test_transient_filter_rejects_other_moment_phrasings(zeev):
    """Other same-class junk found in the live table, plus the historical
    "nieces will be flying to London" fabrication CLAUDE.md records."""
    for f in ("Alex is looking for Shrimp Sauce on Amazon",
              "Alex is searching for something on Amazon",
              "Alex is currently near Boston, Massachusetts",
              "Alex's nieces will be flying to London",
              "Alex is driving to Boston tonight",
              "Alex just finished a job interview",
              "Alex is heading out this weekend"):
        assert zeev._is_transient_fact(f), f


def test_transient_filter_keeps_durable_facts(zeev):
    """The filter is deliberately narrow. A blanket "is <verb>ing" rule would
    eat most of the real list -- these are all genuine rows from the live
    table and every one must survive."""
    for f in ("Alex is interviewing with Google for a Technical Account "
              "Manager (TAM) role in Cloud Consulting",
              "Alex is actively job searching for production support, "
              "solutions engineering, or technical account management roles "
              "in Greater Boston",
              "Alex is learning to play guitar and enjoys jazz in the evenings",
              "Alex is building a Raspberry Pi AI companion called Zeev",
              "Alex is married to Maria",
              "Alex has a cat named Smokey",
              "Alex speaks Hebrew fluently",
              "Alex lives in Canton, Massachusetts (near Boston)"):
        assert not zeev._is_transient_fact(f), f


def test_extract_memory_drops_transient_fact_end_to_end(zeev, monkeypatch):
    """The filter must actually be wired into the merge loop, not just exist.
    A durable fact in the same response still gets through."""
    monkeypatch.setattr(zeev, "USER_FACTS", ["Alex enjoys jazz music."])
    saved = {}
    monkeypatch.setattr(zeev, "save_memory", lambda facts: saved.update(f=list(facts)))

    payload = json.dumps({"facts": ["Alex is going to drive safely",
                                    "Alex has a dog named Leo"]})
    with patch.object(zeev, "_feiergente_complete", return_value=(payload, None)):
        result = zeev.extract_memory([
            {"role": "user", "content": "bye"},
            {"role": "assistant", "content": "Drive safely, Alex."},
        ])

    assert result == ["Alex enjoys jazz music.", "Alex has a dog named Leo"]
    assert "Alex is going to drive safely" not in saved["f"]
