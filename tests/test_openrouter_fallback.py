"""Tests for _groq_post_with_fallback()'s OpenRouter candidate selection.

Found live 2026-08-06: Groq rate-limited a Torah question mid-conversation,
fell back to OpenRouter's "openrouter/free" -- a router that "selects free
models at random" from OpenRouter's entire free catalog, per OpenRouter's
own model description. It landed on a content-safety classifier, not a chat
model, and its "User Safety: safe / Response Safety: safe" classifier
output was returned and spoken to Alex as if it were a real answer to
"tell me the story about Ezekiel" -- syntactically a valid completion, so
nothing caught it.

Two bugs, both in this function:
1. The fallback model was the random full-catalog router, which can land on
   a non-chat model. Fixed with _OPENROUTER_FREE_CANDIDATES, a short ordered
   list of specific, verified chat-capable models.
2. `if not or_err` only catches a connection-level failure (_openai_compat_post
   returns (resp, None) even on a 429/error HTTP status) -- a rate-limited or
   otherwise-failed OpenRouter response was being accepted as a successful
   completion and handed to the caller. Fixed by also checking
   `or_resp.status_code == 200`.
"""
from unittest.mock import MagicMock, patch


def _groq_429_then_none(*a, **kw):
    resp = MagicMock()
    resp.status_code = 429
    return resp, None


def test_fallback_never_uses_the_random_free_router(zeev):
    """The literal string "openrouter/free" must never be requested -- it's
    OpenRouter's own random-selection router across the entire free catalog,
    not a specific chat model."""
    requested_models = []

    def fake_openai_compat_post(url, api_key, msgs, model, stream, max_tokens):
        requested_models.append(model)
        resp = MagicMock()
        resp.status_code = 200
        return resp, None

    with patch.object(zeev, "_groq_post", side_effect=_groq_429_then_none), \
         patch.object(zeev, "_openai_compat_post", side_effect=fake_openai_compat_post), \
         patch.object(zeev, "OPENROUTER_API_KEY", "fake-key"):
        zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "llama-3.3-70b-versatile")

    assert requested_models, "expected at least one OpenRouter attempt"
    assert "openrouter/free" not in requested_models


def test_fallback_rejects_non_200_and_tries_next_candidate(zeev):
    """A rate-limited (or otherwise failed) OpenRouter response must not be
    accepted as a successful completion -- `or_err` alone doesn't catch an
    HTTP-level failure, only a connection-level one."""
    calls = []

    def fake_openai_compat_post(url, api_key, msgs, model, stream, max_tokens):
        calls.append(model)
        resp = MagicMock()
        if len(calls) == 1:
            resp.status_code = 429  # first candidate is rate-limited
        else:
            resp.status_code = 200
        return resp, None

    # Candidate count is independent of this test -- it exercises the loop
    # logic itself, not how many real candidates happen to be configured.
    with patch.object(zeev, "_OPENROUTER_FREE_CANDIDATES", ["model-a", "model-b"]), \
         patch.object(zeev, "_groq_post", side_effect=_groq_429_then_none), \
         patch.object(zeev, "_openai_compat_post", side_effect=fake_openai_compat_post), \
         patch.object(zeev, "OPENROUTER_API_KEY", "fake-key"):
        resp, err = zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "llama-3.3-70b-versatile")

    assert len(calls) == 2, "must move on to a second candidate after a 429"
    assert resp.status_code == 200


def test_fallback_returns_groq_response_if_no_openrouter_key(zeev):
    """No OPENROUTER_API_KEY -- must not attempt any OpenRouter call and
    must return the original (rate-limited) Groq response/error untouched."""
    groq_resp = MagicMock()
    groq_resp.status_code = 429

    with patch.object(zeev, "_groq_post", return_value=(groq_resp, None)), \
         patch.object(zeev, "_openai_compat_post") as mock_or, \
         patch.object(zeev, "OPENROUTER_API_KEY", ""):
        resp, err = zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "llama-3.3-70b-versatile")

    mock_or.assert_not_called()
    assert resp is groq_resp


def test_fallback_returns_groq_response_when_all_candidates_fail(zeev):
    """If every OpenRouter candidate fails, fall back to the original Groq
    response/error rather than returning a broken OpenRouter one."""
    groq_resp = MagicMock()
    groq_resp.status_code = 429

    def always_429(url, api_key, msgs, model, stream, max_tokens):
        resp = MagicMock()
        resp.status_code = 429
        return resp, None

    with patch.object(zeev, "_groq_post", return_value=(groq_resp, None)), \
         patch.object(zeev, "_openai_compat_post", side_effect=always_429), \
         patch.object(zeev, "OPENROUTER_API_KEY", "fake-key"):
        resp, err = zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "llama-3.3-70b-versatile")

    assert resp is groq_resp


def test_candidate_list_excludes_known_reasoning_only_models(zeev):
    """Regression guard: nvidia/nemotron-nano-9b-v2:free (and cousins from
    the same reasoning-model family) stream their entire output under
    `delta.reasoning`, leaving `delta.content` permanently empty --
    _iter_llm_tokens only reads `delta.content`, so this produces a silent
    empty reply. Found live 2026-08-06. Don't re-add it (or any other
    unverified reasoning-shaped model) without checking a raw streaming
    response first, not just a non-streaming one."""
    known_broken = {
        "nvidia/nemotron-nano-9b-v2:free",
        "openai/gpt-oss-20b:free",
        "cohere/north-mini-code:free",
        "inclusionai/ling-3.0-tiny:free",
        "poolside/laguna-s-2.1:free",
    }
    assert not (known_broken & set(zeev._OPENROUTER_FREE_CANDIDATES))
    assert len(zeev._OPENROUTER_FREE_CANDIDATES) >= 1
