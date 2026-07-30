"""Replies must not narrate themselves; stage directions get spoken aloud.

Observed live 2026-07-30 on the house-camera path, all read out verbatim by TTS:
"<voice>Sarina: ...</voice>", "(Sarina speaks in a composed, professional female
voice.)" and "Zeev's thoughts are processing your query. Please wait." The
vision models produce these most, getting the persona prompt without the
conversational context that otherwise discourages it.

The negatives matter: a mid-sentence parenthetical is usually real content, and
stripping it would silently change what Zeev says.
"""
import pytest


@pytest.mark.parametrize("raw,must_go,must_stay", [
    ("<voice>Sarina: Zeev is processing your question. Please wait.</voice>\n\n"
     "<voice>Sarina: I can see you, Alex.</voice>",
     ["<voice>", "Sarina:", "Please wait"], "I can see you, Alex."),
    ("(Sarina speaks in a composed, professional female voice.)\n\n"
     "Alex, the feed looks still.",
     ["composed", "professional"], "the feed looks still"),
    ("[Sarina, calm tone] The door is closed.", ["calm tone"], "The door is closed."),
    ("Zeev's thoughts are processing your query. Please wait. The room is quiet.",
     ["processing", "Please wait"], "The room is quiet."),
    # Markdown-bold labels, from the basement camera 2026-07-30. The `*` are not
    # stripped until _clean_for_tts's markdown pass, which runs AFTER the label
    # pass -- so the label used to survive, shed its asterisks, and be spoken as
    # "Sarina colon".
    ("**Sarina:** The room is heavily cluttered.\n\n"
     "**Zeev:** Chaos precedes order, Alex.",
     ["Sarina:", "Zeev:"], "The room is heavily cluttered"),
    ("*Sarina:* The door is open.", ["Sarina:"], "The door is open."),
    ("__Zeev:__ It is quiet here.", ["Zeev:"], "It is quiet here."),
])
def test_stage_directions_removed(zeev, raw, must_go, must_stay):
    out = zeev._clean_for_tts(raw)
    for frag in must_go:
        assert frag.lower() not in out.lower(), f"{frag!r} survived in {out!r}"
    assert must_stay.lower() in out.lower(), out


@pytest.mark.parametrize("text", [
    "The meeting (which you moved) is at four.",
    "I found it in the kitchen. It looks fine.",
    "Your sister (Maria) called about the tickets.",
    "He said the voice on the phone sounded familiar.",
])
def test_real_content_is_preserved(zeev, text):
    """A parenthetical mid-sentence, and the bare word 'voice', are content."""
    out = zeev._clean_for_tts(text)
    assert out.rstrip(".") .lower() in text.lower() or len(out) >= len(text) - 2, out
    for word in text.replace("(", "").replace(")", "").split():
        if word.isalpha() and len(word) > 3:
            assert word.lower() in out.lower(), f"lost {word!r} from {text!r}"


def test_empty_and_plain_are_safe(zeev):
    assert zeev._clean_for_tts("") == ""
    assert "hello there" in zeev._clean_for_tts("hello there").lower()
