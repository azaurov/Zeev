"""The system prompt must carry the wall clock on every turn.

Without it the model has no clock at all and confabulates rather than
declining. Observed live 2026-07-29, three consecutive turns: "I don't have the
current time", then "it's 8:45 AM", then -- after being told to recalibrate --
"it's currently 2:45 PM". The Pi's own clock read 21:03 EDT throughout, and
timedatectl was correctly set to America/New_York. The only wall clock anywhere
in the file was inside the tool-calling prompt, which ordinary chat never sees.
"""
import re


def test_now_str_includes_timezone(zeev):
    """The zone is the part that had to be there, and the easiest to lose.

    `datetime.now()` is naive, so `%Z` formats as '' and the zone silently
    disappears -- the string still looks fine, which is what makes it a trap.
    """
    s = zeev._now_str()
    assert s.strip(), "clock string is empty"
    assert re.search(r"\b[A-Z]{2,5}\b$", s), f"no timezone at end of {s!r}"


def test_now_str_has_date_and_time(zeev):
    s = zeev._now_str()
    assert re.search(r"\b(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\b", s), s
    assert re.search(r"\b\d{4}\b", s), f"no year in {s!r}"
    assert re.search(r"\d{1,2}:\d{2}\s*(AM|PM)", s), f"no clock time in {s!r}"


def test_clock_is_in_every_prompt(zeev):
    """Not gated on any regex -- an ungated turn is the case that broke."""
    for text in ["hello", "tell me a joke", "what time is it", "как дела"]:
        p = zeev._build_system_prompt(text)
        assert "## Right now:" in p, f"clock missing for {text!r}"
        assert zeev._now_str()[:22] in p, f"clock value missing for {text!r}"
