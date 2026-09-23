"""Free.ai as the LAST Groq-429 chat fallback (added 2026-09-23).

Its free tier is 30K tokens/day (~15 calls), so it must only be reached when
Groq, Requesty, Cloudflare and every OpenRouter candidate have all failed --
never ahead of a provider with a bigger pool.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _all_keys(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "REQUESTY_API_KEY", "rq-key")
    monkeypatch.setattr(zeev, "CLOUDFLARE_API_KEY", "cf-key")
    monkeypatch.setattr(zeev, "CLOUDFLARE_AI_URL", "https://cf.example/ai/v1/chat/completions")
    monkeypatch.setattr(zeev, "OPENROUTER_API_KEY", "or-key")
    monkeypatch.setattr(zeev, "FREEAI_API_KEY", "fa-key")


def _groq_429(*a, **kw):
    resp = MagicMock()
    resp.status_code = 429
    return resp, None


def _resp(status, tag=None):
    r = MagicMock()
    r.status_code = status
    r.tag = tag
    return r


def test_freeai_answers_only_after_every_other_provider_failed(zeev):
    urls = []

    def fake_post(url, api_key, msgs, model, stream, max_tokens):
        urls.append(url)
        return (_resp(200, url) if url == zeev.FREEAI_URL else _resp(429)), None

    with patch.object(zeev, "_groq_post", side_effect=_groq_429), \
         patch.object(zeev, "_openai_compat_post", side_effect=fake_post):
        resp, err = zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "openai/gpt-oss-20b")

    assert resp.status_code == 200 and resp.tag == zeev.FREEAI_URL
    assert urls[-1] == zeev.FREEAI_URL and urls.count(zeev.FREEAI_URL) == 1
    assert urls[0] == zeev.REQUESTY_URL
    assert zeev.CLOUDFLARE_AI_URL in urls
    assert sum("openrouter" in u for u in urls) == len(zeev._OPENROUTER_FREE_CANDIDATES)


def test_freeai_untouched_when_an_earlier_provider_answers(zeev):
    urls = []

    def fake_post(url, api_key, msgs, model, stream, max_tokens):
        urls.append(url)
        return _resp(200, url), None

    with patch.object(zeev, "_groq_post", side_effect=_groq_429), \
         patch.object(zeev, "_openai_compat_post", side_effect=fake_post):
        zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "openai/gpt-oss-20b")

    assert zeev.FREEAI_URL not in urls


def test_freeai_failure_returns_the_original_groq_error(zeev):
    groq_resp = _resp(429)
    with patch.object(zeev, "_groq_post", return_value=(groq_resp, None)), \
         patch.object(zeev, "_openai_compat_post", side_effect=lambda *a: (_resp(429), None)):
        resp, err = zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "openai/gpt-oss-20b")
    assert resp is groq_resp


def test_freeai_candidates_exclude_rejected_models(zeev):
    assert zeev._FREEAI_CHAT_CANDIDATES == ["qwen3-8b"]
