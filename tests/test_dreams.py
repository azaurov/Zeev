"""Zeev and Sarina dreaming overnight.

The feature only works if it behaves like memory rather than like a random
number generator. That means ALL the chance is spent once, at dream time, and
stored -- ask twice and you must get the same answer. Roll at question time and
"I don't remember" followed by a full dream reads as a machine improvising.
"""
import datetime as dt
import random
import pathlib
import pytest


@pytest.fixture
def db(zeev, tmp_path, monkeypatch):
    monkeypatch.setattr(zeev, "ZEEV_DB", tmp_path / "d.db")
    monkeypatch.setattr(zeev, "_db_con", None)
    yield zeev
    try:
        zeev._db_con.close()
    except Exception:
        pass
    zeev._db_con = None


# --- the recall model ------------------------------------------------------

def test_recall_is_deterministic(zeev):
    """The property the whole illusion rests on."""
    for _ in range(50):
        assert zeev.dream_recall(0.82, 0) == "vivid"
        assert zeev.dream_recall(0.40, 0) == "fragment"
        assert zeev.dream_recall(0.05, 0) == "none"


def test_dreams_fade_with_age(zeev):
    """A vivid dream is vivid this morning, thinner tomorrow, gone by the week."""
    v = 0.95
    assert zeev.dream_recall(v, 0) == "vivid"
    assert zeev.dream_recall(v, 3) == "fragment"
    assert zeev.dream_recall(v, 7) == "none"


def test_fading_never_goes_backwards(zeev):
    order = {"none": 0, "fragment": 1, "vivid": 2}
    for v in (0.1, 0.35, 0.6, 0.85, 1.0):
        seen = [order[zeev.dream_recall(v, d)] for d in range(0, 9)]
        assert seen == sorted(seen, reverse=True), f"recall improved with age at v={v}"


def test_vivid_dreams_are_rare(zeev):
    """"Seldomly vivid" is the requirement -- most nights leave little."""
    rng = random.Random(7)
    vs = [zeev.roll_dream_vividness(rng) for _ in range(3000)]
    kinds = [zeev.dream_recall(v, 0) for v in vs]
    vivid = kinds.count("vivid") / len(kinds)
    none = kinds.count("none") / len(kinds)
    assert 0.05 < vivid < 0.20, f"vivid rate {vivid:.2f} should be uncommon"
    assert none > 0.4, f"most dreams should leave nothing, got {none:.2f}"


# --- night boundaries ------------------------------------------------------

@pytest.mark.parametrize("hour,expected", [
    (2, "2026-08-02"),    # 2am belongs to the night before
    (4, "2026-08-02"),
    (6, "2026-08-03"),    # morning is its own day again
    (23, "2026-08-03"),
])
def test_night_straddles_midnight(zeev, hour, expected):
    """"Last night" at breakfast means the small hours that just passed."""
    assert zeev.dream_night_date(dt.datetime(2026, 8, 3, hour)) == expected


# --- storage ---------------------------------------------------------------

def test_one_dream_per_persona_per_night(db):
    """zeev-device restarts overnight; a 2am restart must not dream twice."""
    z = db
    assert z.save_dream("zeev", "2026-08-02", "a library", "shelves", 0.9)
    assert not z.save_dream("zeev", "2026-08-02", "another", "other", 0.9)
    assert z.save_dream("sarina", "2026-08-02", "a hallway", "smoke", 0.5)
    with z._db_lock:
        n = z._db().execute("SELECT count(*) c FROM dreams").fetchone()["c"]
    assert n == 2, "both personas dream, but only once each"


def test_asking_twice_gives_the_same_answer(db):
    """The regression that would break the illusion outright."""
    z = db
    night = z.dream_night_date()
    z.save_dream("zeev", night, "a library of shifting shelves", "whispering pages", 0.93)
    first = z.latest_dream("zeev")[1]
    for _ in range(20):
        assert z.latest_dream("zeev")[1] == first


def test_personas_dream_separately(db):
    z = db
    night = z.dream_night_date()
    z.save_dream("zeev", night, "a library", "shelves", 0.95)
    z.save_dream("sarina", night, "a hallway", "smoke", 0.05)
    assert z.latest_dream("zeev")[1] == "vivid"
    assert z.latest_dream("sarina")[1] == "none"
    assert "library" in z.dream_reply("zeev")
    assert "library" not in z.dream_reply("sarina")


def test_dreamless_and_forgotten_are_different_answers(db):
    """"I don't think I dreamt" vs "I did, but it's gone" -- most of the
    texture is in that distinction."""
    z = db
    dreamless = z.dream_reply("zeev")            # nothing stored at all
    z.save_dream("zeev", z.dream_night_date(), "something", "a shape", 0.02)
    forgotten = z.dream_reply("zeev")
    assert "dreamt at all" in dreamless or "Nothing last night" in dreamless
    assert "gone" in forgotten or "can't reach" in forgotten
    assert dreamless != forgotten


def test_old_dreams_are_not_offered_as_last_night(db):
    z = db
    z.save_dream("zeev", "2026-01-01", "an ancient dream", "dust", 1.0)
    row, recall = z.latest_dream("zeev")
    assert row is None and recall == "none"


# --- dreams must never become memories -------------------------------------

def test_dreams_do_not_touch_messages_or_facts(db):
    """Fabricated content in `messages` gets embedded by the maintenance loop
    within 30 minutes and served back as fact -- the exact failure the camera
    hallucinations caused."""
    z = db
    z.save_dream("zeev", z.dream_night_date(), "a library", "shelves", 0.9)
    with z._db_lock:
        con = z._db()
        assert con.execute("SELECT count(*) c FROM messages").fetchone()["c"] == 0
        assert con.execute("SELECT count(*) c FROM facts").fetchone()["c"] == 0


# --- the gate --------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "did you dream?", "any dreams last night?", "what did you dream about?",
    "do you dream?", "tell me about your dreams", "did you sleep well?",
    "did you dream anything?",
])
def test_asking_them_about_dreams(zeev, text):
    assert zeev._DREAM_RE.search(text)


@pytest.mark.parametrize("text", [
    "I had a dream last night",      # Alex describing HIS night
    "I dreamt about my mother",
    "my dream was strange",
    "I have a dream",
    "remind me about my dream journal",
])
def test_alex_talking_about_his_own_dreams_is_not_a_question(zeev, text):
    """Without a second-person cue Zeev answers about himself while Alex is
    trying to tell him something."""
    assert not zeev._DREAM_RE.search(text)


# --- composition -----------------------------------------------------------

def test_compose_splits_dream_from_fragment(zeev):
    out = ("I walk a library whose shelves rearrange themselves.\n"
           "FRAGMENT: shelves made of whispering pages")
    content, frag = zeev.compose_dream("zeev", "-", llm=lambda msgs: out)
    assert "FRAGMENT" not in content
    assert frag == "shelves made of whispering pages"


def test_fragment_falls_back_to_something_fragmentary(zeev):
    """A model that ignores the format must not produce a tidy summary as the
    "fragment" -- partial recall should sound partial."""
    content, frag = zeev.compose_dream(
        "zeev", "-", llm=lambda msgs: "I am walking through a long corridor that keeps folding back.")
    assert frag and len(frag.split()) <= 6


def test_compose_survives_an_empty_model(zeev):
    assert zeev.compose_dream("zeev", "-", llm=lambda msgs: "") == (None, None)


def test_dream_once_reports_a_dreamless_night(db):
    """Some nights simply produce nothing -- that is the design, not a failure."""
    z = db

    class AlwaysSkip:
        def random(self):
            return 1.0
    assert z.dream_once("zeev", rng=AlwaysSkip()) == "dreamless"
