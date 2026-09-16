"""Tests for _looks_like_noise(), the post-STT wake-false-positive gate.

Added 2026-09-16. A wake-word false positive costs more than one wasted
reply: it plants a user/assistant pair in `messages`, which then feeds RAG
and extract_memory -- that is how "Alex is going to drive safely" became a
stored fact and gave Zeev a six-week "safe travels" tic (see
tests/test_memory_extraction.py). Dropping the false positive before
handle_transcript stops the poison at the source.

The gate is deliberately tiny, and these tests exist mostly to keep it that
way. The obvious design -- reject short transcripts -- was measured against
the live DB and abandoned: 167 distinct user utterances of three words or
fewer, including "Bye.", "Yes.", "Hello", "peace", "leave" and a bare "2".
What survives is the one rule with no false-positive risk, validated against
all 1287 user messages in the live DB (only bare "." was rejected): a
transcript with no vowel-bearing Latin word in it.
"""


def test_rejects_the_real_false_positive(zeev):
    """"Ct" -- the actual 2026-09-16 00:19 wake false positive (hey_zeev,
    score 0.85) that drew Alex's "Bye." and Zeev's "Goodbye, Alex. Safe
    driving.\""""
    assert zeev._looks_like_noise("Ct")
    assert zeev._looks_like_noise("Ct.")


def test_rejects_other_non_word_artefacts(zeev):
    for t in ("Hmm", "Mm", "Shh", "Tsk", "Pfft", ".", "...", "?!"):
        assert zeev._looks_like_noise(t), t


def test_keeps_real_short_utterances(zeev):
    """Every one of these is a genuine utterance from the live messages
    table. A word-count gate would have eaten all of them."""
    for t in ("Bye.", "Yes.", "No.", "Okay.", "Hello", "Hey.", "peace",
              "leave", "continue", "test", "you", "2", "Zeev.",
              "French lady.", "Adidas shaman", "Thank you.", "Tell me more.",
              "increase volume", "Show me fire", "Good night."):
        assert not zeev._looks_like_noise(t), t


def test_keeps_non_latin_script(zeev):
    """Hebrew writes no vowels at all, so the vowel rule cannot judge it --
    and Cyrillic/Arabic turns are already in the live DB. All must pass."""
    for t in ("Дешма", "Скажи мне шутку.", "أيزاف سيدشماء", "שמע ישראל", "שלום"):
        assert not zeev._looks_like_noise(t), t


def test_keeps_a_grammatical_false_positive(zeev):
    """Honest scope: this real 2026-09-14 false positive is a well-formed
    sentence and the gate does NOT catch it. Pinned so nobody later widens
    the gate to try -- that direction eats real speech. The remaining fix
    for this class is wake-model retraining, not a text heuristic."""
    assert not zeev._looks_like_noise(
        "Peeces off and puts them right back in the ground.")


def test_empty_is_not_noise(zeev):
    """Empty is the caller's own separate branch ("Didn't catch that"), and
    the follow-up listener already returns "" for silence -- the gate must
    not claim it, or those paths log a misleading noise-gate drop."""
    assert not zeev._looks_like_noise("")
    assert not zeev._looks_like_noise("   ")
    assert not zeev._looks_like_noise(None)
