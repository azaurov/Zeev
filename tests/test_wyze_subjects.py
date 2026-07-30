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
