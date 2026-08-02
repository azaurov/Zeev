"""Singing down a live phone call.

Live 2026-08-02: Alex asked Zeev to ring Maria and sing to her. The call
connected, Maria was transcribed perfectly, and she asked for the song FOUR
times. Each time Zeev answered "I'm sorry, I didn't catch that" -- which was
untrue twice over: he had heard her exactly, and the failure was that llm_fn
returned empty. Nothing in the code connected the rendered duet to the call
audio, so even the successful turn only ever promised a song.
"""
import pytest

MARIA_LOG = [{"role": "caller",
              "text": "Hi, how are you doing? This is Maria. Thank you for calling me."}]


@pytest.mark.parametrize("text", [
    "Yes, please can you sing a happy birthday song for me?",
    "Can you sing a song for me? Happy birthday?",
    "Sing the happy birthday song.",
])
def test_a_caller_asking_for_the_song_is_recognised(zeev, text):
    """The exact phrasings Maria used, in order."""
    assert zeev._BIRTHDAY_SONG_RE.search(text)


def test_name_comes_from_the_call_not_from_the_device(zeev):
    """_birthday_song_name defaults to Alex, which is right on the device and
    wrong on a call -- Alex is the one who DIALLED."""
    assert zeev.call_song_name("and sing her the happy birthday duet", MARIA_LOG) == "Maria"
    assert zeev._birthday_song_name("and sing her the happy birthday duet") == "Alex"


def test_name_from_the_request_wins(zeev):
    assert zeev.call_song_name("sing happy birthday to Maria", []) == "Maria"


@pytest.mark.parametrize("intent,log", [
    ("sing her the song", []),
    ("", [{"role": "caller", "text": "it's a lovely day"}]),
    ("", []),
])
def test_unknown_name_is_none_never_a_guess(zeev, intent, log):
    assert zeev.call_song_name(intent, log) is None


def test_nameless_duet_omits_the_name_entirely(zeev):
    """Singing "dear Alex" at Maria is a wrong answer delivered with total
    confidence -- the same failure class as the fabricated camera reports."""
    lines = zeev.birthday_duet_lines(None)
    joined = " ".join(l for _, l in lines)
    assert "Alex" not in joined
    assert "dear" not in joined.lower()
    assert "birthday" in joined.lower()
    assert len(lines) == 3


def test_named_duet_still_names_them(zeev):
    joined = " ".join(l for _, l in zeev.birthday_duet_lines("Maria"))
    assert "Maria" in joined


def test_sco_playback_needs_ffmpeg(zeev, monkeypatch):
    monkeypatch.setattr(zeev.shutil, "which", lambda *a, **k: None)
    assert zeev.play_wav_sco("/tmp/x.wav", "bluealsa:DEV=x,PROFILE=sco", 16000) is False


def test_sco_playback_declines_a_missing_render(zeev):
    """render_birthday_duet returns None when Orpheus is unavailable; that must
    not be handed to aplay."""
    assert zeev.play_wav_sco(None, "bluealsa:DEV=x,PROFILE=sco", 16000) is False
