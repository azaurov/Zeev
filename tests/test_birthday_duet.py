"""Zeev and Sarina singing together.

The second branch (after goodnight) where both personas speak. It exists
because of a real turn: asked twice on 2026-08-01 to "sing a birthday song
together with Zeev", the request fell through to the 8B, which answered alone
in a single voice while narrating "(sung in harmony)" -- describing a duet
instead of performing one.
"""
import pytest


def _fires(zeev, text):
    return (bool(zeev._BIRTHDAY_SONG_RE.search(text[:70]))
            and not zeev._TOOL_INTENT_RE.search(text))


@pytest.mark.parametrize("text", [
    "Can you sing a happy, beautiful birthday song together with Zeev?",
    "sing me a birthday song",
    "can you and Sarina sing happy birthday to Maria?",
    "birthday duet please",
    "serenade Leo for his birthday",
])
def test_requests_reach_the_duet(zeev, text):
    assert _fires(zeev, text)


@pytest.mark.parametrize("text", [
    "remind me to sing happy birthday to Leo at six",   # a reminder
    "put Maria's birthday on my calendar",              # a calendar write
    "play some jazz",
    "when is Maria's birthday?",                        # a question, not a request
    "what's on my calendar",
])
def test_other_intents_are_left_alone(zeev, text):
    assert not _fires(zeev, text)


@pytest.mark.parametrize("text,name", [
    ("Can you sing a happy birthday song together with Zeev?", "Alex"),
    ("sing happy birthday to Maria", "Maria"),
    ("sing a birthday song for Leo", "Leo"),
    ("sing happy birthday for me", "Alex"),
    ("can you and Sarina sing happy birthday to Smokey?", "Smokey"),
    ("sing a birthday song for my wife", "Alex"),
])
def test_recipient(zeev, text, name):
    """"with Zeev"/"with Sarina" names the duet PARTNER, never the person being
    sung to -- singing "happy birthday dear Zeev" to Alex is the failure."""
    assert zeev._birthday_song_name(text) == name


def test_partner_names_are_never_the_recipient(zeev):
    for text in ("sing happy birthday to Zeev and Sarina",
                 "sing a birthday song with Sarina"):
        assert zeev._birthday_song_name(text) not in ("Zeev", "Sarina")


def test_voices_take_turns_in_blocks(zeev):
    """Blocks, not line-by-line: Zeev takes a verse, Sarina takes a verse, Zeev
    closes. Swapping every line chopped it into six short utterances instead of
    giving each persona a phrase to shape."""
    voices = [v for v, _ in zeev.birthday_duet_lines("Alex")]
    assert set(voices) == {"daniel", "sarina"}, "both personas must sing"
    assert voices[0] == "daniel", "Zeev leads, as in the goodnight pair"
    assert voices[-1] == "daniel", "and Zeev finishes it off"
    assert any(a != b for a, b in zip(voices, voices[1:])), "they must hand over"


def test_the_name_is_actually_sung(zeev):
    lines = zeev.birthday_duet_lines("Maria")
    assert any("Maria" in line for _, line in lines)
    assert not any("Alex" in line for _, line in lines)


def test_the_closing_line_speaks_for_both(zeev):
    """Sequential playback cannot overlap two voices, so "together" is carried
    by the words. Zeev closes, so he is the one who says it is from both."""
    voice, last = zeev.birthday_duet_lines("Alex")[-1]
    assert voice == "daniel"
    assert "Sarina" in last and "me" in last


def test_a_duet_request_naming_no_song_is_not_assumed_to_be_birthday(zeev):
    """"Yes, but can you sing in harmony together with Zeev?" -- the real
    follow-up from 2026-08-01 -- names no song. This branch only knows Happy
    Birthday, and answering an unspecified request with it would be a wrong
    answer confidently delivered, so it goes to the LLM instead."""
    assert not _fires(zeev, "Yes, but can you sing in harmony together with Zeev?")
