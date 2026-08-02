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
    assert set(voices) == {"daniel", zeev._DUET_SARINA_VOICE}, "both personas must sing"
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


def test_sarina_sings_in_her_own_song_voice(zeev):
    """Alex picked Orpheus "autumn" by ear. Scoped to the song only -- "sarina"
    everywhere else still speaks on Kokoro af_heart, so greetings and reminders
    are unchanged."""
    assert zeev._DUET_SARINA_ORPHEUS == "autumn"
    assert zeev._DUET_SARINA_KOKORO == "af_heart"
    voices = [v for v, _ in zeev.birthday_duet_lines("Alex")]
    assert zeev._DUET_SARINA_VOICE in voices
    assert "sarina" not in voices


def test_both_singers_prefer_orpheus(zeev):
    """One engine for the whole song. Zeev on Orpheus against Sarina on Kokoro
    sounded like two takes spliced together -- matched for neither room tone
    nor level."""
    assert set(zeev._ORPHEUS_FIRST) == {"daniel", zeev._DUET_SARINA_VOICE}
    assert zeev._ORPHEUS_FIRST["daniel"] == "daniel"
    assert zeev._ORPHEUS_FIRST[zeev._DUET_SARINA_VOICE] == "autumn"


def test_speaking_sarina_is_not_routed_to_orpheus(zeev):
    """The asymmetry is the whole point of a separate key: ordinary Sarina
    still goes to the Kokoro daemon first."""
    assert "sarina" not in zeev._ORPHEUS_FIRST


def test_the_song_voice_is_a_persona_key_not_a_kokoro_name(zeev):
    """A raw Kokoro name works on the daemon path but reaches Orpheus verbatim
    in the fallback, where no such voice exists -- a turn taken while the daemon
    was down would 400 and drop to espeak."""
    assert not zeev._DUET_SARINA_VOICE.startswith(("af_", "am_", "bf_", "bm_"))


# --- the jazz bed ----------------------------------------------------------

def test_lines_are_exclamatory(zeev):
    """Orpheus takes its energy from punctuation -- full stops read level and
    closing, exclamations lift. The wording was chosen by ear against a
    flatter version of the same lines."""
    for _, line in zeev.birthday_duet_lines("Alex"):
        assert "!" in line


def test_settled_render_parameters(zeev):
    """Every one of these was chosen by ear over several passes on the device;
    they are decisions, not defaults."""
    assert zeev._DUET_BPM == 132          # medium swing, near the reference clip
    assert zeev._DUET_BEAT_GAIN == 0.26   # the duet is the point, jazz sits behind
    assert zeev._DUET_VERSE_TEMPO == [0.92, 0.92, 1.0]


def test_verse_tempo_never_touches_pitch(zeev):
    """atempo changes duration only. A pitch shift here would undo the voices
    exactly as the abandoned singing build did."""
    src = __import__("inspect").getsource(zeev.render_birthday_duet)
    assert "atempo" in src
    assert "rubberband" not in src and "asetrate" not in src


def test_render_degrades_to_none_without_ffmpeg(zeev, monkeypatch):
    """None is an ordinary outcome -- the caller speaks the duet plainly."""
    monkeypatch.setattr(zeev.shutil, "which", lambda *a, **k: None)
    assert zeev.render_birthday_duet("Alex") is None


def test_render_returns_none_when_orpheus_is_unavailable(zeev, monkeypatch, tmp_path):
    """A 429 must not break the song, only downgrade it."""
    monkeypatch.setattr(zeev, "_DUET_CACHE", tmp_path)
    monkeypatch.setattr(zeev.shutil, "which", lambda *a, **k: "/usr/bin/ffmpeg")
    monkeypatch.setattr(zeev, "groq_tts", lambda *a, **k: None)
    assert zeev.render_birthday_duet("Alex") is None


def test_jazz_bed_is_synthesised_at_the_right_length(zeev):
    np = pytest.importorskip("numpy")
    bed = zeev._duet_jazz_bed(4.0, np)
    assert len(bed) == int(4.0 * zeev._DUET_SR)
    assert float(np.max(np.abs(bed))) > 0.05, "the bed must actually contain audio"


def test_swing_is_a_triplet_feel(zeev):
    """Straight eighths with a jazz kit still sound like rock."""
    assert abs(zeev._DUET_SWING - 2 / 3) < 1e-9


# --- a call that mentions singing is a CALL --------------------------------

def test_call_with_a_number_beats_the_duet(zeev):
    """Live 2026-08-02: "Can you call my wife Maria? It's 857-701-7252 and sing
    her the happy birthday song." The duet branch sits ABOVE the phone branch,
    so without this exclusion the song plays in the room and the phone never
    rings -- which is the opposite of what was asked."""
    t = "Can you call my wife Maria? It's 857-701-7252 and sing her the happy birthday song."
    assert zeev._bt_call_match(t), "the call gate must catch it"
    fires = (bool(zeev._BIRTHDAY_SONG_RE.search(t[:zeev._BIRTHDAY_SONG_MAX_START]))
             and not zeev._TOOL_INTENT_RE.search(t)
             and not zeev._bt_call_match(t))
    assert not fires, "the duet must stand down for a call"


def test_song_request_without_a_number_still_sings(zeev):
    t = "Can you sing happy birthday for my mother Sabrina?"
    assert not zeev._bt_call_match(t)
    assert zeev._BIRTHDAY_SONG_RE.search(t[:zeev._BIRTHDAY_SONG_MAX_START])


def test_the_window_reaches_a_late_birthday(zeev):
    """"birthday" landed at character 67 and the old 70-char window cut it off
    three characters short, so even the song missed."""
    t = "Can you call my wife Maria? It's 857-701-7252 and sing her the happy birthday song."
    assert not zeev._BIRTHDAY_SONG_RE.search(t[:70])
    assert zeev._BIRTHDAY_SONG_RE.search(t[:zeev._BIRTHDAY_SONG_MAX_START])
