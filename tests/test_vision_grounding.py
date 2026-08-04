"""Tests for cross-modal grounding via the LLM as the shared space
(docs/research-directions.md #2).

A camera description is already retrievable through the same RAG path as
any other exchange (see CLAUDE.md "History RAG") -- the point of this
feature is the part that's easy to get wrong: a vision reply describes a
moment, not a standing fact, and this project has hit that exact failure
class repeatedly (see the Wyze camera section of CLAUDE.md). `finish_turn`'s
`vision=` flag tags the DB-stored copy of a vision-derived reply with
`_VISION_TAG`; `_build_system_prompt` strips that tag and attaches a
staleness caveat when RAG resurfaces it.
"""
import re
import threading
import types

import pytest


# ---------------------------------------------------------------------------
# finish_turn(vision=...): tags the stored copy only
# ---------------------------------------------------------------------------

@pytest.fixture
def ctx(zeev):
    class _TestCtx(zeev._DeviceCtx):
        pass

    c = _TestCtx()
    c.session = []
    c.spoke, c.faces, c.states = [], [], []
    c.board = types.SimpleNamespace(set_rgb=lambda *a: None)
    c._set_face = lambda state, caption="": c.faces.append((state, caption))
    c._go_idle = lambda: c.states.append("idle")
    c._go_ready = lambda: c.states.append("ready")
    c._speak_device = lambda text, voice="sarina": c.spoke.append(text)
    c._busy = threading.Event()
    c._speak_cancel = threading.Event()
    c._followup_listen = lambda: ""
    c._MORE_YES_RE = re.compile(r"\b(yes)\b", re.I)
    c._LED_ERROR = c._LED_SPEAKING = c._LED_THINKING = (0, 0, 0)
    return c


def test_vision_true_tags_only_the_stored_copy(zeev, ctx, monkeypatch):
    stored = []
    monkeypatch.setattr(zeev, "append_message",
                         lambda role, content: stored.append((role, content)))
    zeev.finish_turn(ctx, "I can see a cat on the couch.", vision=True)

    assert ("assistant", zeev._VISION_TAG + "I can see a cat on the couch.") in stored
    # Nothing spoken or held in the live session carries the tag.
    assert ctx.spoke == ["I can see a cat on the couch."]
    assert ctx.session[-1]["content"] == "I can see a cat on the couch."


def test_vision_false_is_the_default_and_untagged(zeev, ctx, monkeypatch):
    stored = []
    monkeypatch.setattr(zeev, "append_message",
                         lambda role, content: stored.append((role, content)))
    zeev.finish_turn(ctx, "Sure, here's a joke.")

    assert ("assistant", "Sure, here's a joke.") in stored
    assert not any(c.startswith(zeev._VISION_TAG) for r, c in stored)


def test_vision_tag_survives_a_reply_that_itself_starts_with_bracket(zeev, ctx, monkeypatch):
    """The tag is a prefix, not a sentinel the model could forge -- a reply
    that happens to start with '[' must not be misparsed on the way back out."""
    stored = []
    monkeypatch.setattr(zeev, "append_message",
                         lambda role, content: stored.append((role, content)))
    reply = "[grainy] I can't make out much on this camera."
    zeev.finish_turn(ctx, reply, vision=True)
    tagged = stored[-1][1]
    assert tagged == zeev._VISION_TAG + reply
    assert tagged[len(zeev._VISION_TAG):] == reply


# ---------------------------------------------------------------------------
# _build_system_prompt: strips the tag, attaches the staleness caveat
# ---------------------------------------------------------------------------

def test_vision_hit_gets_staleness_caveat_and_tag_is_stripped(zeev, monkeypatch):
    hit = ("did you see Smokey earlier",
           zeev._VISION_TAG + "I can see a grey cat on the basement couch.")
    monkeypatch.setattr(zeev, "retrieve_semantic", lambda *a, **kw: [hit])

    p = zeev._build_system_prompt("is Smokey still there")

    assert zeev._VISION_TAG not in p
    assert "I can see a grey cat on the basement couch." in p
    assert "camera observation from a past moment" in p


def test_ordinary_hit_gets_no_staleness_caveat(zeev, monkeypatch):
    hit = ("what's the capital of France", "Paris.")
    monkeypatch.setattr(zeev, "retrieve_semantic", lambda *a, **kw: [hit])

    p = zeev._build_system_prompt("remind me what we said about France")

    assert "Paris." in p
    assert "camera observation from a past moment" not in p


def test_mixed_hits_only_the_vision_one_gets_the_caveat(zeev, monkeypatch):
    hits = [
        ("what's the capital of France", "Paris."),
        ("check the bedroom cam", zeev._VISION_TAG + "The bed is made, no one's there."),
    ]
    monkeypatch.setattr(zeev, "retrieve_semantic", lambda *a, **kw: hits)

    p = zeev._build_system_prompt("anything come up recently")

    assert p.count("camera observation from a past moment") == 1
    assert "Paris." in p
    assert "The bed is made, no one's there." in p
    assert zeev._VISION_TAG not in p
