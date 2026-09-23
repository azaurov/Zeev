"""Web /chat "draw me ..." -> Cloudflare flux-1-schnell (added 2026-09-23).

The failure this pins is the silent one: a draw request that either fell
through to the chat LLM (which then says it can't make images) or hijacked a
camera/reminder phrasing. Assert the image really comes back.
"""
import base64
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.parametrize("text,subject", [
    ("draw me a cat on a windowsill", "cat on a windowsill"),
    ("Generate an image of a red barn at dusk", "a red barn at dusk"),
    ("can you make me a picture of a dragon?", "a dragon"),
    ("create a watercolor painting of the sea", "the sea"),
    ("please draw me a robot.", "robot"),
])
def test_draw_requests_are_recognised(zeev, text, subject):
    assert zeev.image_gen_subject(text) == subject


@pytest.mark.parametrize("text", [
    "take a picture",
    "what do you see",
    "look at the bedroom camera",
    "remind me to draw a picture of the dog at four",
    "show me the picture you took",
    "how do I draw a circle in CSS",
    "tell me about the drawing on the wall",
    "",
])
def test_non_draw_phrasing_is_left_alone(zeev, text):
    assert zeev.image_gen_subject(text) is None


def test_generate_image_returns_the_image(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "CLOUDFLARE_AI_URL", "https://cf.example/x")
    monkeypatch.setattr(zeev, "CLOUDFLARE_ACCOUNT_ID", "acct")
    b64 = base64.b64encode(b"\xff\xd8jpeg").decode()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"success": True, "result": {"image": b64}}
    with patch.object(zeev.requests, "post", return_value=resp) as post:
        img, err = zeev.generate_image("a cat")
    assert (img, err) == (b64, None)
    assert post.call_args.args[0].endswith("/ai/run/@cf/black-forest-labs/flux-1-schnell")
    assert post.call_args.kwargs["json"]["prompt"] == "a cat"


def test_generate_image_reports_failure_not_success(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "CLOUDFLARE_AI_URL", "https://cf.example/x")
    resp = MagicMock(status_code=429)
    resp.json.return_value = {"success": False, "errors": [{"message": "limit"}]}
    with patch.object(zeev.requests, "post", return_value=resp):
        img, err = zeev.generate_image("a cat")
    assert img is None and err


def test_generate_image_survives_network_error_and_missing_keys(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "CLOUDFLARE_AI_URL", "https://cf.example/x")
    with patch.object(zeev.requests, "post", side_effect=OSError("down")):
        assert zeev.generate_image("a cat")[0] is None
    monkeypatch.setattr(zeev, "CLOUDFLARE_AI_URL", "")
    assert zeev.generate_image("a cat")[0] is None


def test_web_chat_actually_wires_the_gate(zeev):
    """Structural: the /chat handler must emit the image SSE event."""
    import inspect
    src = inspect.getsource(zeev)
    i = src.index("_img_subject = image_gen_subject(user_msg)")
    block = src[i:i + 900]
    assert "generate_image(_img_subject)" in block and 'sse({"image"' in block
