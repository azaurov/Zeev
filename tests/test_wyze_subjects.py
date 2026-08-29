"""Named subjects: "check on Smokey".

Three failure modes are pinned here. A reminder ("remind me to check on Smokey
at four") must not be swallowed by a camera sweep, because this branch sits
above the tool branch. A vision reply that ignores the FOUND format must come
back inconclusive, never as a false negative -- folding it into "no" burns the
next camera and then denies a sighting while holding the description that made
it. And a config typo must skip-and-log, since parsing runs at import.
"""
import pytest

CAMS = ["backyard", "basement-cam", "doorbell-cam", "front-yard",
        "leos-room", "living-room-cam", "secret", "upstairs"]
FLASHED = ["basement-cam", "upstairs"]


@pytest.fixture
def subjects(zeev):
    return zeev.parse_subjects("smokey:cat:basement-cam|upstairs", CAMS)


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

def test_parses_name_kind_and_cameras(zeev, subjects):
    assert subjects["smokey"] == {"name": "smokey", "kind": "cat",
                                  "cams": ["basement-cam", "upstairs"]}


def test_camera_list_defaults_to_directly_reachable(zeev):
    """An omitted list must not sweep six cameras that never answer."""
    subs = zeev.parse_subjects("smokey:cat", CAMS, default_cams=FLASHED)
    assert subs["smokey"]["cams"] == FLASHED


def test_sweep_is_capped(zeev):
    subs = zeev.parse_subjects(
        "smokey:cat:" + "|".join(CAMS), CAMS)
    assert len(subs["smokey"]["cams"]) <= zeev._SUBJECT_MAX_CAMS


def test_unknown_camera_is_dropped_not_fatal(zeev):
    subs = zeev.parse_subjects("smokey:cat:nowhere|upstairs", CAMS)
    assert subs["smokey"]["cams"] == ["upstairs"]


@pytest.mark.parametrize("spec", ["", "   ", "smokey", "smokey:", ":cat",
                                  "smokey:cat:nowhere", "::::"])
def test_bad_config_skips_rather_than_raises(zeev, spec):
    """Parsing runs at import -- a typo may not stop the app from starting."""
    assert zeev.parse_subjects(spec, CAMS) == {}


def test_aliases_cover_whisper_spellings(zeev):
    """Whisper spells a name however it hears it, and a missed alias fails
    silently -- the turn falls through to the LLM, which denies seeing him."""
    subs = zeev.parse_subjects("smokey|smoky|smokie:cat:upstairs", CAMS)
    assert sorted(subs) == ["smokey", "smokie", "smoky"]
    assert {s["name"] for s in subs.values()} == {"smokey"}, "one spoken name"
    assert zeev.resolve_subject("where's Smoky", subs)["name"] == "smokey"


def test_multiple_subjects(zeev):
    subs = zeev.parse_subjects("smokey:cat:upstairs,luna:dog:basement-cam", CAMS)
    assert sorted(subs) == ["luna", "smokey"]
    assert subs["luna"]["kind"] == "dog"


# ---------------------------------------------------------------------------
# Intent resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "check on Smokey",
    "can you check on Smokey",
    "hey Sarina, check on Smokey",
    "where's Smokey",
    "where is Smokey",
    "how's Smokey doing",
    "find Smokey",
    "look for Smokey",
    "what's Smokey up to",
    "keep an eye on Smokey",
])
def test_matches_subject_phrasing(zeev, subjects, text):
    assert zeev.resolve_subject(text, subjects) is not None


@pytest.mark.parametrize("text", [
    "remind me to check on Smokey at four",
    "set a timer to check on Smokey in ten minutes",
    "put check on Smokey on my calendar tomorrow",
])
def test_reminder_phrasing_is_not_a_camera_sweep(zeev, subjects, text):
    """This branch sits above the tool branch; it must not steal reminders."""
    assert zeev.resolve_subject(text, subjects) is None


@pytest.mark.parametrize("text", [
    "Smokey is a good cat",
    "I was telling Dave about Smokey yesterday",
    "what's the weather",
    "check the basement cam",
    "tell me a story about a cat named Smokey who checks on people",
])
def test_ignores_incidental_mentions(zeev, subjects, text):
    assert zeev.resolve_subject(text, subjects) is None


def test_no_subjects_configured_never_matches(zeev):
    assert zeev.resolve_subject("check on Smokey", {}) is None


# ---------------------------------------------------------------------------
# Vision prompt and verdict parsing
# ---------------------------------------------------------------------------

def test_prompt_asks_about_the_kind_not_the_name(zeev, subjects):
    p = zeev.subject_vision_prompt(subjects["smokey"]["kind"], "basement cam")
    assert "cat" in p and "smokey" not in p.lower()
    assert "FOUND" in p


@pytest.mark.parametrize("reply,found", [
    ("FOUND: yes\nA cat is asleep on the couch.", True),
    ("FOUND: no\nAn empty room with a couch.", False),
    ("found yes - the cat is by the door", True),
    ("**FOUND: no**\nJust laundry.", False),
])
def test_parses_verdict(zeev, reply, found):
    seen, desc = zeev.parse_subject_sighting(reply)
    assert seen is found
    assert desc and not desc.lower().startswith("found")


def test_unformatted_reply_is_inconclusive_not_a_miss(zeev):
    seen, desc = zeev.parse_subject_sighting(
        "There's a grey cat curled up on the stairs.")
    assert seen is None
    assert "grey cat" in desc


def test_empty_and_stage_direction_only_replies(zeev):
    assert zeev.parse_subject_sighting("") == (None, "")
    seen, desc = zeev.parse_subject_sighting(
        "FOUND: no\n(Sarina's voice, calm and professional.)")
    assert seen is False
    assert desc == "", "a stage-direction-only description is spoken as silence"


# ---------------------------------------------------------------------------
# Stage labels vs the verdict
# ---------------------------------------------------------------------------

def test_speaker_label_does_not_block_the_verdict(zeev):
    """Live 2026-07-31, bedroom-cam returned:

        "Sarina: FOUND: no. I see a bed with rumpled grey bedding, a fan..."

    _SUBJECT_FOUND_RE anchors to line start, so the label in front of it meant
    a clean "no" parsed as None -- the unparseable case, which is the expensive
    one. Stripping stage directions BEFORE matching fixes the verdict; it was
    previously computed only as an emptiness test and thrown away.
    """
    seen, desc = zeev.parse_subject_sighting(
        "Sarina: FOUND: no. I see a bed with rumpled grey bedding, a fan, "
        "a bookshelf, and a wolf triptych wall hanging.")
    assert seen is False, f"verdict misparsed as {seen}"
    assert "FOUND" not in desc, desc
    assert not desc.lower().startswith("sarina"), desc
    assert "rumpled grey bedding" in desc


def test_description_is_spoken_clean(zeev):
    """`desc` is SPOKEN, not merely tested for emptiness -- the leak reached
    Alex as "On the bedroom cam I can see: Sarina: FOUND: no. I see a bed..."
    """
    for raw in [
        "Sarina: FOUND: yes. A grey cat is asleep on the chair.",
        "(Sarina speaks in a composed, professional voice)\n\nFOUND: yes\n\n"
        "A grey cat is asleep on the chair.",
        "**Sarina:** FOUND: yes. A grey cat is asleep on the chair.",
    ]:
        seen, desc = zeev.parse_subject_sighting(raw)
        assert seen is True, raw
        assert "FOUND" not in desc, desc
        assert "Sarina" not in desc, desc
        assert "grey cat" in desc, desc


def test_unparseable_still_returns_none(zeev):
    """Stripping must not turn a genuinely format-ignoring reply into a "no" --
    that folding is the mistake the three-state verdict exists to avoid."""
    seen, desc = zeev.parse_subject_sighting(
        "Sarina: I see a cluttered room filled with books and furniture.")
    assert seen is None
    assert "cluttered room" in desc
    assert not desc.lower().startswith("sarina"), desc


# ---------------------------------------------------------------------------
# A "no" that contradicts its own description
# ---------------------------------------------------------------------------

def test_no_verdict_mentioning_the_subject_is_uncertain(zeev):
    """Live 2026-07-30, smokeys-cam returned FOUND: no alongside "...a window,
    and a cat" -- 79 chars, so unlike the "cat tree" case in CLAUDE.md this was
    not log truncation. Zeev reported a confident "I didn't see Smokey".

    Downgraded to uncertain rather than flipped to yes: the model is
    demonstrably unreliable on that frame, so the honest output quotes what it
    saw and lets Alex judge.
    """
    seen, desc = zeev.parse_subject_sighting(
        "FOUND: no. I can see a cluttered bedroom containing a bed, "
        "bookshelves, a window, and a cat", kind="cat")
    assert seen is None, seen
    assert "cat" in desc


def test_plain_no_stays_no(zeev):
    """The guard must not turn every miss into a maybe -- "I didn't see him" is
    the right answer when the description really has no cat in it."""
    seen, _ = zeev.parse_subject_sighting(
        "FOUND: no. I see a bed with rumpled grey bedding, a fan and a "
        "bookshelf.", kind="cat")
    assert seen is False, seen


def test_yes_is_unaffected(zeev):
    seen, _ = zeev.parse_subject_sighting(
        "FOUND: yes. A grey cat is asleep on the chair.", kind="cat")
    assert seen is True


def test_guard_is_word_bounded(zeev):
    """"cat" must not match inside "cluttered"/"catalogue" -- a substring hit
    would make every miss uncertain and the branch would stop ever saying no."""
    seen, _ = zeev.parse_subject_sighting(
        "FOUND: no. A cluttered room with a catalogue on the table.", kind="cat")
    assert seen is False, seen


def test_no_kind_keeps_old_behaviour(zeev):
    """Callers that pass no kind (and the existing tests) are unchanged."""
    seen, _ = zeev.parse_subject_sighting("FOUND: no. I see a cat.")
    assert seen is False


def test_negated_mention_does_not_trigger_the_guard(zeev):
    """"no cat is visible" mentions the word but negates it -- this is a
    genuine miss, not a contradiction. Live 2026-08-29, bedroom-cam: "There
    is no dog visible in the image; instead, a multi-panel wolf artwork
    hangs above the bed." was downgraded to uncertain when it should have
    stayed a clean "no"."""
    seen, _ = zeev.parse_subject_sighting(
        "FOUND: no. There is no dog visible in the image; instead, a "
        "multi-panel wolf artwork hangs above the bed.", kind="dog")
    assert seen is False, seen


def test_furniture_compound_does_not_trigger_the_guard(zeev):
    """"a cat tree" names furniture, not a cat -- live 2026-08-29,
    smokeys-cam: "...a cat tree, and various household items, but no cat is
    visible" was downgraded to uncertain by the furniture mention alone."""
    seen, _ = zeev.parse_subject_sighting(
        "FOUND: no. The room contains a window, bookshelves, a cat tree, "
        "and various household items, but no cat is visible.", kind="cat")
    assert seen is False, seen


def test_dog_bed_furniture_compound_does_not_trigger_the_guard(zeev):
    seen, _ = zeev.parse_subject_sighting(
        "FOUND: no. I can see an empty dog bed by the window.", kind="dog")
    assert seen is False, seen
