"""Splitting a mixed-script reply so each run is spoken in its own language.

_speak_device picks ONE language for the whole string and the Hebrew-script
check wins, so a reply quoting Hebrew inside an English sentence was read
end-to-end by a Hebrew voice. Live 2026-07-31 that was most of a prayer answer.
"""
import pytest

REAL = ('The Bedtime Angelic Prayer, also known as the Kriyat Shema Al HaMita\', '
        'starts with "Hear, O Israel, the Lord is our God, the Lord is one" in '
        'English, and "שְׁמַע יִשְׂרָאֵל יְהוָה אֱלֹהֵינוּ יְהוָה אֶחָד" in Hebrew, '
        'and I\'d be happy to help you recite it.')


def test_pure_english_is_one_run(zeev):
    """A single-script reply must behave exactly as before -- one run means
    _speak_device does not recurse at all."""
    runs = zeev.split_by_script("The siddur is the Jewish prayer book.")
    assert len(runs) == 1 and runs[0][0] == "en"


def test_pure_hebrew_is_one_run(zeev):
    runs = zeev.split_by_script("שְׁמַע יִשְׂרָאֵל")
    assert len(runs) == 1 and runs[0][0] == "he"


def test_real_mixed_reply_splits(zeev):
    runs = zeev.split_by_script(REAL)
    langs = [l for l, _ in runs]
    assert langs.count("he") == 1, langs
    assert langs.count("en") == 2, langs
    he = next(c for l, c in runs if l == "he")
    assert "שְׁמַע" in he and "אֶחָד" in he
    # The English halves must not carry Hebrew letters into an English voice.
    for lang, chunk in runs:
        if lang == "en":
            assert not zeev._HE_RE.search(chunk), chunk


def test_nothing_is_lost(zeev):
    """Concatenating the runs must reproduce the input -- a dropped fragment is
    a silently unspoken clause."""
    for text in [REAL, "hello שלום world", "שלום hello", "abc", "שלום"]:
        assert "".join(c for _, c in zeev.split_by_script(text)) == text


def test_punctuation_stays_with_its_run(zeev):
    """Quotes and commas around a Hebrew phrase belong to it, not to their own
    fragment -- each extra run costs an audible engine switch."""
    runs = zeev.split_by_script('he said "שלום" loudly')
    assert len(runs) == 3, runs
    assert '"' in runs[1][1] or '"' in runs[0][1]


def test_stray_letter_does_not_trigger_a_switch(zeev):
    """A single Hebrew character inside English is folded in: the pause between
    TTS calls is worse than the mispronunciation."""
    runs = zeev.split_by_script("the letter א is aleph")
    assert len(runs) == 1, runs


def test_russian_is_handled_too(zeev):
    runs = zeev.split_by_script("he said Привет мир to me")
    assert [l for l, _ in runs] == ["en", "ru", "en"], runs


def test_empty_and_blank(zeev):
    assert zeev.split_by_script("") == []
    assert zeev.split_by_script("   ") == []
