"""Tests for the refusal -> feiergente qwen2.5 fallback.

Found live 2026-08-22: GPT-OSS-20B flatly refused "Regarding the Lindsey
Clancy's Court Case, can you use the Talmudic knowledge to form an opinion
about it?" with "I'm sorry, but I can't help with that." feiergente01's
qwen2.5:7b-instruct-q4_K_M, asked the same question directly, engaged with
it instead (built a Talmudic-ethics framework around the case). This wires
that model in as a one-shot retry when a canned refusal is detected, rather
than changing the primary model or routing.
"""
from unittest.mock import patch


def test_looks_like_refusal_matches_canned_refusal(zeev):
    assert zeev._looks_like_refusal("I'm sorry, but I can't help with that.")
    assert zeev._looks_like_refusal("I cannot provide legal commentary on real cases.")
    assert zeev._looks_like_refusal("As an AI language model, I can't offer opinions on this.")


def test_looks_like_refusal_matches_curly_apostrophe(zeev):
    # The model's own output used curly quotes ("I’m sorry, but I
    # can’t help with that.") -- a straight-quote-only regex silently
    # never matched this, found live the same night the path was added.
    assert zeev._looks_like_refusal("I’m sorry, but I can’t help with that.")


def test_looks_like_refusal_false_for_normal_reply(zeev):
    assert not zeev._looks_like_refusal(
        "Absolutely -- Jewish law has a rich tradition of looking at justice "
        "through a Talmudic lens. When the Sages discuss a court case they "
        "often start with a few guiding principles..."
    )
    assert not zeev._looks_like_refusal("")
    assert not zeev._looks_like_refusal(None)


def test_looks_like_refusal_false_for_long_reply_even_if_it_matches(zeev):
    # A long, substantive reply that happens to open with an apology must
    # not be treated as a refusal -- only the short canned shape counts.
    long_reply = "I'm sorry, but let me explain in detail. " + ("Talmudic reasoning. " * 30)
    assert len(long_reply) > 220
    assert not zeev._looks_like_refusal(long_reply)


def test_refusal_fallback_reply_returns_none_without_feiergente_url(zeev):
    with patch.object(zeev, "FEIERGENTE_URL", ""):
        assert zeev._refusal_fallback_reply("question", "system prompt") is None


def test_refusal_fallback_reply_uses_feiergente_completion(zeev):
    with patch.object(zeev, "FEIERGENTE_URL", "http://feiergente.example"), \
         patch.object(zeev, "_feiergente_complete", return_value=("A real, engaged answer.", None)) as mock_complete:
        result = zeev._refusal_fallback_reply("question", "system prompt")
    assert result == "A real, engaged answer."
    msgs = mock_complete.call_args[0][0]
    assert msgs[0] == {"role": "system", "content": "system prompt"}
    assert msgs[1] == {"role": "user", "content": "question"}


def test_refusal_fallback_reply_returns_none_on_error(zeev):
    with patch.object(zeev, "FEIERGENTE_URL", "http://feiergente.example"), \
         patch.object(zeev, "_feiergente_complete", return_value=(None, "connection refused")):
        assert zeev._refusal_fallback_reply("question", "system prompt") is None


def test_refusal_fallback_reply_returns_none_if_feiergente_also_refuses(zeev):
    with patch.object(zeev, "FEIERGENTE_URL", "http://feiergente.example"), \
         patch.object(zeev, "_feiergente_complete", return_value=("I'm sorry, but I can't help with that.", None)):
        assert zeev._refusal_fallback_reply("question", "system prompt") is None


def test_search_regex_covers_court_case_phrasing(zeev):
    # Grants Tavily grounding to real-world-case questions so a fallback
    # reply (or the primary one) has actual facts to work with instead of a
    # hypothetical, per the same 2026-08-22 incident.
    assert zeev.needs_search("Regarding the Lindsey Clancy court case, what happened?")
    assert zeev.needs_search("What was the verdict in that lawsuit?")
    assert zeev.needs_search("Tell me about the trial")
