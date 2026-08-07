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
