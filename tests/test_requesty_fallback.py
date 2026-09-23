"""Requesty (router.requesty.ai) as a second free-tier fallback provider.

Added 2026-09-23. OpenRouter's free tier is 50 requests/DAY account-wide and
the dog detector's Leo check spends the same pool -- on 2026-09-22 it ran out
and Leo wasn't called. Requesty's free models have their own 200/day
allowance. Chat fallback tries Requesty first (spares OpenRouter for the dog
detector); vision tries it after the free OpenRouter entries but before the
paid one.

These pin what the user experiences: when Groq is rate-limited the reply
actually comes from Requesty, a Requesty failure still reaches OpenRouter,
and a vision call works with only a Requesty key.
"""
from unittest.mock import MagicMock, patch


def _groq_429(*a, **kw):
    resp = MagicMock()
    resp.status_code = 429
    return resp, None


def _ok(tag):
    resp = MagicMock()
    resp.status_code = 200
    resp.tag = tag
    return resp


def test_groq_429_is_answered_by_requesty_before_openrouter(zeev):
    calls = []

    def fake_post(url, api_key, msgs, model, stream, max_tokens):
        calls.append((url, api_key, model))
        return _ok(url), None

    with patch.object(zeev, "_groq_post", side_effect=_groq_429), \
         patch.object(zeev, "_openai_compat_post", side_effect=fake_post), \
         patch.object(zeev, "REQUESTY_API_KEY", "rq-key"), \
         patch.object(zeev, "OPENROUTER_API_KEY", "or-key"):
        resp, err = zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "openai/gpt-oss-20b")

    assert err is None
    assert resp.status_code == 200
    assert resp.tag == zeev.REQUESTY_URL, "reply must come from Requesty"
    assert calls == [(zeev.REQUESTY_URL, "rq-key", zeev._REQUESTY_FREE_CANDIDATES[0])]
    assert not any("openrouter" in c[0] for c in calls), "OpenRouter quota spent needlessly"


def test_requesty_failure_still_reaches_openrouter(zeev):
    urls = []

    def fake_post(url, api_key, msgs, model, stream, max_tokens):
        urls.append(url)
        if url == zeev.REQUESTY_URL:
            resp = MagicMock()
            resp.status_code = 429
            return resp, None
        return _ok(url), None

    with patch.object(zeev, "_groq_post", side_effect=_groq_429), \
         patch.object(zeev, "_openai_compat_post", side_effect=fake_post), \
         patch.object(zeev, "REQUESTY_API_KEY", "rq-key"), \
         patch.object(zeev, "OPENROUTER_API_KEY", "or-key"):
        resp, err = zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "openai/gpt-oss-20b")

    assert resp.status_code == 200
    assert "openrouter" in resp.tag
    assert urls[0] == zeev.REQUESTY_URL
    assert any("openrouter" in u for u in urls[1:])


def test_no_requesty_key_never_calls_requesty(zeev):
    urls = []

    def fake_post(url, api_key, msgs, model, stream, max_tokens):
        urls.append(url)
        return _ok(url), None

    with patch.object(zeev, "_groq_post", side_effect=_groq_429), \
         patch.object(zeev, "_openai_compat_post", side_effect=fake_post), \
         patch.object(zeev, "REQUESTY_API_KEY", ""), \
         patch.object(zeev, "OPENROUTER_API_KEY", "or-key"):
        zeev._groq_post_with_fallback([{"role": "user", "content": "hi"}], "openai/gpt-oss-20b")

    assert zeev.REQUESTY_URL not in urls


def test_requesty_candidates_exclude_models_that_failed_streaming_benchmark(zeev):
    """Each of these returned an empty or monologue reply (or 404/410) in the
    2026-09-23 raw streaming benchmark. Don't re-add without re-testing."""
    rejected = {
        "google/gemma-4-31b-it",
        "nvidia/muse-glimmer-30b",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/nemotron-3.5-content-safety",
        "novita/inclusionai/ling-3.0-tiny",
        "nvidia/nemotron-3-nano-30b-a3b",
        "poolside/laguna-m.1",
        "poolside/laguna-xs.2",
    }
    assert zeev._REQUESTY_FREE_CANDIDATES
    assert not (rejected & set(zeev._REQUESTY_FREE_CANDIDATES))


def _vision_ok(text):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": text}}]}
    return resp


def test_vision_requesty_entry_hits_requesty_with_bare_model_id(zeev):
    seen = []

    def fake_post(url, headers, json, timeout):
        seen.append((url, headers["Authorization"], json["model"]))
        return _vision_ok("A cat on the couch.")

    with patch.object(zeev, "VISION_MODELS", ["requesty:vendor/model-x"]), \
         patch.object(zeev, "REQUESTY_API_KEY", "rq-key"), \
         patch.object(zeev, "OPENROUTER_API_KEY", "or-key"), \
         patch.object(zeev, "requests") as fake_requests:
        fake_requests.post.side_effect = fake_post
        text, err = zeev.vision_complete("ZmFrZQ==", "what's here?")

    assert text == "A cat on the couch."
    assert seen == [(zeev.REQUESTY_URL, "Bearer rq-key", "vendor/model-x")]


def test_vision_works_with_only_a_requesty_key(zeev):
    """Used to return 'no OPENROUTER_API_KEY' before trying anything."""
    seen = []

    def fake_post(url, headers, json, timeout):
        seen.append(url)
        return _vision_ok("An empty hallway.")

    with patch.object(zeev, "VISION_MODELS", ["or/model-a", "requesty:vendor/model-x"]), \
         patch.object(zeev, "REQUESTY_API_KEY", "rq-key"), \
         patch.object(zeev, "OPENROUTER_API_KEY", ""), \
         patch.object(zeev, "requests") as fake_requests:
        fake_requests.post.side_effect = fake_post
        text, err = zeev.vision_complete("ZmFrZQ==", "what's here?")

    assert text == "An empty hallway."
    assert seen == [zeev.REQUESTY_URL], "OpenRouter entry must be skipped without its key"


def test_vision_skips_requesty_entry_without_its_key(zeev):
    seen = []

    def fake_post(url, headers, json, timeout):
        seen.append(json["model"])
        return _vision_ok("A tree.")

    with patch.object(zeev, "VISION_MODELS", ["requesty:vendor/model-x", "or/model-a"]), \
         patch.object(zeev, "REQUESTY_API_KEY", ""), \
         patch.object(zeev, "OPENROUTER_API_KEY", "or-key"), \
         patch.object(zeev, "requests") as fake_requests:
        fake_requests.post.side_effect = fake_post
        text, err = zeev.vision_complete("ZmFrZQ==", "what's here?")

    assert text == "A tree."
    assert seen == ["or/model-a"]
