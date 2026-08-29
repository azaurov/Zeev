"""Tests for vision_complete()'s cross-model retry.

Found live 2026-08-29: sweep_for_subject's cron report hit "Worker local
total request limit reached (16/16)" on nemotron-omni and HTTP 429 on gemma
after only 4 total vision calls, with the Pi's own device mode confirmed
idle at the time -- this account didn't exhaust anything itself, it landed
in a moment of shared free-tier congestion on OpenRouter's side. That kind
of congestion often clears within seconds, so vision_complete() now retries
the whole model list up to VISION_RETRIES more times after a short delay
instead of giving up after one pass.
"""
from unittest.mock import MagicMock, patch


def test_retries_the_whole_model_list_after_a_full_failure(zeev):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        resp = MagicMock()
        if len(calls) <= 2:
            resp.status_code = 429  # both models fail on the first pass
        else:
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [{"message": {"content": "A cat on the couch."}}]
            }
        return resp

    with patch.object(zeev, "VISION_MODELS", ["model-a", "model-b"]), \
         patch.object(zeev, "OPENROUTER_API_KEY", "fake-key"), \
         patch.object(zeev, "VISION_RETRIES", 1), \
         patch.object(zeev, "VISION_RETRY_DELAY_S", 0), \
         patch.object(zeev, "requests") as fake_requests:
        fake_requests.post.side_effect = fake_post
        text, err = zeev.vision_complete("ZmFrZQ==", "what's here?")

    assert text == "A cat on the couch."
    assert err is None
    # first pass fails both models; second pass succeeds on the first one
    assert calls == ["model-a", "model-b", "model-a"]


def test_gives_up_after_all_retries_exhausted(zeev):
    def fake_post(url, headers, json, timeout):
        resp = MagicMock()
        resp.status_code = 429
        return resp

    with patch.object(zeev, "VISION_MODELS", ["model-a"]), \
         patch.object(zeev, "OPENROUTER_API_KEY", "fake-key"), \
         patch.object(zeev, "VISION_RETRIES", 2), \
         patch.object(zeev, "VISION_RETRY_DELAY_S", 0), \
         patch.object(zeev, "requests") as fake_requests:
        fake_requests.post.side_effect = fake_post
        text, err = zeev.vision_complete("ZmFrZQ==", "what's here?")

    assert text is None
    assert "429" in err
    assert fake_requests.post.call_count == 3  # 1 initial + 2 retries


def test_no_retry_sleep_when_the_first_pass_succeeds(zeev):
    def fake_post(url, headers, json, timeout):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "An empty hallway."}}]
        }
        return resp

    with patch.object(zeev, "VISION_MODELS", ["model-a"]), \
         patch.object(zeev, "OPENROUTER_API_KEY", "fake-key"), \
         patch.object(zeev, "VISION_RETRIES", 3), \
         patch.object(zeev, "requests") as fake_requests, \
         patch.object(zeev, "time") as fake_time:
        fake_requests.post.side_effect = fake_post
        text, err = zeev.vision_complete("ZmFrZQ==", "what's here?")

    assert text == "An empty hallway."
    fake_time.sleep.assert_not_called()
