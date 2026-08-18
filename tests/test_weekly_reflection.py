"""weekly_reflection.py -- <think> stripping and empty-after-strip guard on
its Groq fallback path.

Regression coverage for the same bug class news_digest.py hit live
2026-08-18 (see CLAUDE.md's "World news" section): qwen/qwen3.6-27b inlines
hidden reasoning into `content`, and on a large-enough prompt that reasoning
can consume the whole completion budget, leaving either an unstripped
<think> block or, once stripped, an empty "reflection". Fixed the same way:
switch to gpt-oss-20b + reasoning_effort="low", strip defensively regardless,
and treat empty-after-strip as an error rather than a successful stored
reflection.

Requires GROQ_API_KEY at import time (the module exits if it's missing), so
set a dummy one before importing if the environment doesn't already have it.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("GROQ_API_KEY", "test-key-not-real")

sys.path.insert(0, str(Path(__file__).parent.parent / "zeev"))
import weekly_reflection as wr  # noqa: E402


def test_strip_think_text_removes_closed_block():
    text = "<think>reasoning about the week</think>Alex seemed energized this week."
    assert wr._strip_think_text(text) == "Alex seemed energized this week."


def test_strip_think_text_removes_unclosed_block():
    text = "<think>still working through the transcript and never got to the reflection"
    assert wr._strip_think_text(text) == ""


def test_strip_think_text_leaves_normal_text_alone():
    text = "Alex seemed energized this week, no reasoning trace at all."
    assert wr._strip_think_text(text) == text


def test_groq_model_is_not_qwen():
    """qwen3.6-27b is the model this bug was originally found on -- pin the
    fix so a future edit doesn't drift back to it without re-deriving why."""
    assert wr.GROQ_MODEL != "qwen/qwen3.6-27b"
    assert "qwen" not in wr.GROQ_MODEL.lower()


def test_synthesize_rejects_groq_reply_empty_after_strip(monkeypatch):
    """An all-reasoning, unclosed-<think>, zero-content Groq reply must be
    treated as a failure, not silently stored as a terse-but-valid reflection
    -- the same fix world_news.summarize() got for news_digest.py."""
    monkeypatch.setattr(wr, "_call_feiergente", lambda p: (None, "not configured"))
    monkeypatch.setattr(wr, "_call_bosgame", lambda p: (None, "not configured"))
    monkeypatch.setattr(
        wr, "_call_groq",
        lambda p: ("<think>still reasoning and never reached the reflection", None),
    )
    content, err = wr._synthesize("some transcript", 7)
    assert content is None
    assert "empty" in err.lower()


def test_synthesize_strips_think_from_groq_reply(monkeypatch):
    monkeypatch.setattr(wr, "_call_feiergente", lambda p: (None, "not configured"))
    monkeypatch.setattr(wr, "_call_bosgame", lambda p: (None, "not configured"))
    monkeypatch.setattr(
        wr, "_call_groq",
        lambda p: ("<think>pick the themes</think>Alex focused on the new project this week.", None),
    )
    content, err = wr._synthesize("some transcript", 7)
    assert err is None
    assert content == "Alex focused on the new project this week."


def test_synthesize_strips_think_from_bosgame_reply(monkeypatch):
    """Defensive stripping applies to every LLM path, not just Groq's --
    bosgame doesn't currently inline reasoning, but a future model swap
    shouldn't get a silent pass just because it isn't the Groq branch."""
    monkeypatch.setattr(wr, "_call_feiergente", lambda p: (None, "not configured"))
    monkeypatch.setattr(
        wr, "_call_bosgame",
        lambda p: ("<think>internal</think>Alex asked about the thermal camera project.", None),
    )
    content, err = wr._synthesize("some transcript", 7)
    assert err is None
    assert content == "Alex asked about the thermal camera project."
