"""Cloudflare Workers AI as a vision provider and Groq-429 chat fallback.

Added 2026-09-23. Workers AI's free 10,000 neurons/day is its own pool.
Vision: llama-4-scout read a text-dense flyer in 2.4s where nemotron-omni
took ~34s, so "cloudflare:" entries lead VISION_MODELS. Chat: llama-3.3-70b
is tried after Requesty, before OpenRouter.

Found while benchmarking: Workers AI's OpenAI-compatible stream sends
digit-only tokens as JSON numbers -- `"delta": {"content": 12}` -- on every
model checked. _iter_llm_tokens_raw yielded that int straight through, so
the first reply with a number in it ("that's 12 dollars") would raise
TypeError wherever tokens are joined into the reply: a dead turn.
"""
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _only_cloudflare(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "REQUESTY_API_KEY", "")
    monkeypatch.setattr(zeev, "ANYAPI_API_KEY", "")
    monkeypatch.setattr(zeev, "CLOUDFLARE_API_KEY", "cf-key")
    monkeypatch.setattr(zeev, "CLOUDFLARE_AI_URL", "https://cf.example/ai/v1/chat/completions")


def _sse(*contents):
    lines = [b"data: " + json.dumps({"choices": [{"delta": {"content": c}}]}).encode()
             for c in contents]
    resp = MagicMock()
    resp.iter_lines.return_value = lines + [b"data: [DONE]"]
    return resp


def test_numeric_stream_tokens_become_text(zeev):
    """The exact shape Workers AI sent live: '12' as a JSON number."""
    resp = _sse("That's ", 12, " dollars, in ", 1945, ".")
    toks = list(zeev._iter_llm_tokens(resp, "groq"))
    assert all(isinstance(t, str) for t in toks), toks
    assert "".join(toks) == "That's 12 dollars, in 1945."


def _groq_429(*a, **kw):
    resp = MagicMock()
    resp.status_code = 429
    return resp, None


def test_groq_429_is_answered_by_cloudflare_before_openrouter(zeev):
    calls = []

    def fake_post(url, api_key, msgs, model, stream, max_tokens):
        calls.append((url, api_key, model))
        resp = MagicMock()
        resp.status_code = 200
        resp.tag = url
        return resp, None

    with patch.object(zeev, "_groq_post", side_effect=_groq_429), \
         patch.object(zeev, "_openai_compat_post", side_effect=fake_post), \
         patch.object(zeev, "OPENROUTER_API_KEY", "or-key"):
        resp, err = zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "openai/gpt-oss-20b")

    assert resp.tag == zeev.CLOUDFLARE_AI_URL, "reply must come from Cloudflare"
    assert calls == [(zeev.CLOUDFLARE_AI_URL, "cf-key", zeev._CLOUDFLARE_CHAT_CANDIDATES[0])]


def test_cloudflare_sits_between_requesty_and_openrouter(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "REQUESTY_API_KEY", "rq-key")
    urls = []

    def fake_post(url, api_key, msgs, model, stream, max_tokens):
        urls.append(url)
        resp = MagicMock()
        resp.status_code = 429
        return resp, None

    with patch.object(zeev, "_groq_post", side_effect=_groq_429), \
         patch.object(zeev, "_openai_compat_post", side_effect=fake_post), \
         patch.object(zeev, "OPENROUTER_API_KEY", "or-key"):
        zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "openai/gpt-oss-20b")

    assert urls[0] == zeev.REQUESTY_URL
    assert urls[1] == zeev.CLOUDFLARE_AI_URL
    assert all("openrouter" in u for u in urls[2:]) and len(urls) > 2


def test_no_cloudflare_url_never_calls_cloudflare(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "CLOUDFLARE_AI_URL", "")
    urls = []

    def fake_post(url, api_key, msgs, model, stream, max_tokens):
        urls.append(url)
        resp = MagicMock()
        resp.status_code = 200
        return resp, None

    with patch.object(zeev, "_groq_post", side_effect=_groq_429), \
         patch.object(zeev, "_openai_compat_post", side_effect=fake_post), \
         patch.object(zeev, "OPENROUTER_API_KEY", "or-key"):
        zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "openai/gpt-oss-20b")

    assert "" not in urls and all("cf.example" not in u for u in urls)


def test_chat_candidates_exclude_models_that_failed_benchmark(zeev):
    rejected = {"@cf/meta/llama-3.1-8b-instruct-fp8",   # "Sarina's voice:" prefix
                "@cf/google/gemma-4-26b-a4b-it",         # all reasoning, empty
                "@cf/qwen/qwen3.8-27b"}                  # all reasoning, empty
    assert zeev._CLOUDFLARE_CHAT_CANDIDATES
    assert not (rejected & set(zeev._CLOUDFLARE_CHAT_CANDIDATES))


def _vision_ok(text):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": text}}]}
    return resp


def test_vision_cloudflare_entry_hits_workers_ai_with_bare_model_id(zeev):
    seen = []

    def fake_post(url, headers, json, timeout):
        seen.append((url, headers["Authorization"], json["model"]))
        return _vision_ok("A lit tent by a campfire.")

    with patch.object(zeev, "VISION_MODELS", ["cloudflare:@cf/vendor/model-z"]), \
         patch.object(zeev, "OPENROUTER_API_KEY", ""), \
         patch.object(zeev, "requests") as fake_requests:
        fake_requests.post.side_effect = fake_post
        text, err = zeev.vision_complete("ZmFrZQ==", "what's here?")

    assert text == "A lit tent by a campfire."
    assert seen == [(zeev.CLOUDFLARE_AI_URL, "Bearer cf-key", "@cf/vendor/model-z")]


def test_vision_skips_cloudflare_without_account_url(zeev, monkeypatch):
    """Key present but no CLOUDFLARE_ACCOUNT_ID -> URL is "" -> must skip,
    never POST to an empty URL."""
    monkeypatch.setattr(zeev, "CLOUDFLARE_AI_URL", "")
    seen = []

    def fake_post(url, headers, json, timeout):
        seen.append(url)
        return _vision_ok("A tree.")

    with patch.object(zeev, "VISION_MODELS", ["cloudflare:@cf/vendor/model-z", "or/model-a"]), \
         patch.object(zeev, "OPENROUTER_API_KEY", "or-key"), \
         patch.object(zeev, "requests") as fake_requests:
        fake_requests.post.side_effect = fake_post
        text, err = zeev.vision_complete("ZmFrZQ==", "what's here?")

    assert text == "A tree."
    assert len(seen) == 1 and "openrouter" in seen[0]


def test_production_vision_list_leads_with_cloudflare(zeev):
    assert zeev.VISION_MODELS[0] == "cloudflare:@cf/meta/llama-4-scout-17b-16e-instruct"
    assert zeev.VISION_MODELS[1] == "cloudflare:@cf/mistralai/mistral-small-3.1-24b-instruct"
