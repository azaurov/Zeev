"""Gates for capabilities that existed but were unreachable from device mode.

Music, quantum, notes and thermal were all implemented and wired into the web
UI and terminal REPL only — the Pi, the primary surface, was the least capable
of the three front-ends. These tests cover the gates added to close that, and
in particular the collisions that make a new gate dangerous: it sits in a
chain of ~19 other regexes and a greedy one silently hijacks ordinary speech.
"""
import pytest


# ---------------------------------------------------------------------------
# Music stop
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "stop the music",
    "pause the song",
    "turn off the music",
    "kill the music",
    "stop playback",
    "stop it",
    "pause that",
])
def test_music_stop_matches(zeev, text):
    assert zeev._MUSIC_STOP_RE.search(text) is not None, text


@pytest.mark.parametrize("text", [
    "stop talking",
    "stop the reminder",
    "what is the weather",
    "tell me a story",
    "can you stop by the store later",
])
def test_music_stop_ignores_other_speech(zeev, text):
    """A greedy stop gate would swallow ordinary sentences containing 'stop'."""
    assert zeev._MUSIC_STOP_RE.search(text) is None, text


# ---------------------------------------------------------------------------
# Music play — collisions with neighbouring gates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,query", [
    ("play Dark Side of the Moon", "Dark Side of the Moon"),
    ("put on some Miles Davis", "some Miles Davis"),
    ("play me some jazz", "some jazz"),
])
def test_music_play_extracts(zeev, text, query):
    assert zeev.extract_music_query(text) == query


@pytest.mark.parametrize("text", [
    "show me the fire effect",
    "show me the matrix",
])
def test_visual_requests_are_not_music(zeev, text):
    """The music gate sits after the visual gates; it must also not match these
    on its own, or ordering becomes the only thing preventing a bad search."""
    assert zeev.extract_music_query(text) is None, text


def test_music_and_stop_are_mutually_exclusive(zeev):
    """No phrase may look like both a play and a stop request."""
    for text in ["play Dark Side of the Moon", "put on some Miles Davis",
                 "stop the music", "pause the song"]:
        is_play = zeev.extract_music_query(text) is not None
        is_stop = zeev._MUSIC_STOP_RE.search(text) is not None
        assert not (is_play and is_stop), text


def test_music_does_not_collide_with_tool_intents(zeev):
    """Reminders and music are both new gates; they must not overlap."""
    for text in ["remind me to call Dave at 4", "set a timer for 10 minutes"]:
        assert zeev.extract_music_query(text) is None, text
        assert zeev._MUSIC_STOP_RE.search(text) is None, text


def test_tool_intents_are_not_music_stop(zeev):
    assert zeev._MUSIC_STOP_RE.search("cancel the reminder") is None


# ---------------------------------------------------------------------------
# Quantum
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "quantum: should I take the job",
    "run a quantum circuit on whether to move",
    "use quantum reasoning to analyze my options",
])
def test_quantum_extracts_idea(zeev, text):
    idea = zeev.extract_quantum_query(text)
    assert idea and len(idea) > 3, text


@pytest.mark.parametrize("text", [
    "what is quantum computing",
    "tell me about quantum mechanics",
    "play some jazz",
])
def test_quantum_ignores_ordinary_mentions(zeev, text):
    """Talking *about* quantum must not launch a circuit simulation."""
    assert zeev.extract_quantum_query(text) is None, text


# ---------------------------------------------------------------------------
# Whole-chain sanity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "how are you doing today",
    "what's the weather like",
    "tell me about the Talmud",
])
def test_plain_chat_matches_no_actuator_gate(zeev, text):
    """Ordinary conversation must fall through to the LLM, untouched by any of
    the gates added for actuators."""
    assert zeev.extract_music_query(text) is None
    assert zeev._MUSIC_STOP_RE.search(text) is None
    assert zeev.extract_quantum_query(text) is None
    assert zeev._TOOL_INTENT_RE.search(text) is None
