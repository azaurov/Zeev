"""Detecting a reply that was cut off mid-sentence.

finish_turn treats "truncated" as hard evidence the model ran out of tokens, so
a false positive is expensive and user-visible: it appends "Want to hear more?"
to a COMPLETE reply and speaks it, rewrites the stored history to the modified
text, arms the follow-up listener, and burns an LLM call pre-generating a
continuation nobody asked for.
"""
import pytest

# Observed live 2026-08-01 19:07 -- logged "truncated to last sentence
# (113/114 chars)". The one missing character was the closing quote, and Alex
# was asked "Want to hear more?" after a complete birthday song.
LIVE_REPLY = ('(sung in harmony) "Happy birthday to you, happy birthday to you, '
              'happy birthday dear Alex, happy birthday to you!"')


def _cut_short(zeev, text):
    stripped = text.strip()
    return (zeev._last_complete_sentence(stripped) or text) != stripped


@pytest.mark.parametrize("text", [
    LIVE_REPLY,
    'She replied, "yes!"',
    "He said, \"hello.\" Then he left.",
    "Done (for now).",
    "Plain and complete.",
    "Ends with an ellipsis...",
])
def test_complete_replies_are_not_flagged(zeev, text):
    assert not _cut_short(zeev, text)


@pytest.mark.parametrize("text", [
    "This is complete. And this frag",
    'He said, "hello." Then he trailed o',
])
def test_genuine_truncation_still_detected(zeev, text):
    """The rule has to keep working -- dropping a dangling fragment before TTS
    is the whole reason it exists."""
    assert _cut_short(zeev, text)


def test_no_terminator_at_all_is_spoken_whole(zeev):
    """None means "speak it whole", matching the rule _stream_speak follows --
    a short reply with no full stop is not a truncated one."""
    assert zeev._last_complete_sentence("No terminator here") is None


def test_trailing_quote_is_kept_in_the_spoken_text(zeev):
    assert zeev._last_complete_sentence(LIVE_REPLY) == LIVE_REPLY


# --- follow-up affirmatives -----------------------------------------------
#
# Observed in the same 19:07 turn: "Yes, but can you sing in harmony together
# with Zeev?" matched the affirmative regex on its first word, so the canned
# pre-generated continuation was delivered and Alex's actual question was never
# answered -- or even seen by the model.

import re

_YES = re.compile(
    r'\b(yes|yeah|yep|sure|go ahead|please|tell me|more|continue|expand|elaborate|details?)\b',
    re.IGNORECASE)


@pytest.mark.parametrize("answer", [
    "yes", "yeah go on", "sure", "tell me more", "more please", "yes please continue",
])
def test_bare_yes_still_delivers_the_detail(zeev, answer):
    assert zeev._plain_affirmative(answer, _YES)


@pytest.mark.parametrize("answer", [
    "Yes, but can you sing in harmony together with Zeev?",
    "yes but what about the weather",
    "actually, tell me about something else",
    "sure, though what time is it?",
])
def test_a_yes_carrying_a_new_request_is_not_a_bare_yes(zeev, answer):
    assert not zeev._plain_affirmative(answer, _YES)


@pytest.mark.parametrize("answer", ["no", "no thanks", "", None])
def test_declines_are_not_affirmative(zeev, answer):
    assert not zeev._plain_affirmative(answer, _YES)
