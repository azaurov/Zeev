"""Whisper's stock noise artefacts.

Given silence or room noise, Whisper does not return an empty string -- it
returns a confident, well-formed closing line from its training data. The
`\\w{2,}` noise guard cannot catch those: they are perfectly good words.

Live 2026-08-02 15:13 a false wake transcribed as "Thank you.", Zeev answered
"You're welcome, Alex.", and both halves were embedded into semantic memory,
where a fabricated exchange can resurface in a later prompt as fact. The call
loop had filtered these since it was written; the wake path had copied only
half its guard while the comment claimed parity.
"""
import pytest


@pytest.mark.parametrize("text", [
    "Thank you.", "thank you", "Thanks", "thanks.", "You.", "Please.",
    "Goodbye.", "Bye.", "Beep.", "beep beep", "Bing", "Bong",
    "Thank you for watching.", "Thanks for watching!",
    "Thank you for listening.", "I don't know", "I dont know",
    "", "   ",
])
def test_stock_artefacts_are_filtered(zeev, text):
    assert zeev._is_whisper_hallucination(text)


@pytest.mark.parametrize("text", [
    "Thank you for the reminder about therapy",
    "thanks, can you play some jazz?",
    "Say goodbye to Maria for me",
    "What's this week's Torah portion?",
    "the video. The video is detailed. When researchers analyze the mitocons",
    "Can you help me with angel prayer?",
])
def test_real_speech_is_never_filtered(zeev, text):
    """Matched whole, never as a substring -- a genuine "thank you for the
    reminder" has to get through, or the filter costs more than it saves."""
    assert not zeev._is_whisper_hallucination(text)


def test_none_is_treated_as_empty(zeev):
    assert zeev._is_whisper_hallucination(None)
