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
    # "have" is the verb here, not "dream" -- found live 2026-08-03 falling
    # through to the LLM, which said it can't dream (true for a language
    # model with no dream table, but wrong: the gate just never fired).
    "did you have any dreams last night?", "do you have dreams?",
    "did you ever have a dream?",
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


# --- quantum-resolved choice point ------------------------------------------

def _fake_qllm(spec_json):
    """A qllm stub matching quantum._llm_circuit_spec's (text, err) contract."""
    def _call(msgs, max_tokens=300, json_mode=False):
        return spec_json, None
    return _call


def test_resolve_dream_choice_picks_the_certain_branch(zeev):
    """theta=pi on one option and 0 on the other is a certain outcome after
    H-RZ-H, not a coin flip -- pin the actual interference math, not just
    that *some* branch gets picked."""
    import json
    spec_json = json.dumps({
        "options": ["open the door", "look out the window"],
        "phases": [3.14159265, 0.0],
        "entangled_pairs": [],
        "reflection": "a fork",
    })
    chosen, spec, result = zeev._resolve_dream_choice(
        "In a dream: a hallway forks. Two paths appear.",
        qllm=_fake_qllm(spec_json))
    assert chosen == "open the door"
    assert spec["options"] == ["open the door", "look out the window"]
    assert result["probabilities"]


def test_resolve_dream_choice_can_dissolve_to_nothing(zeev):
    """Zero phase on every option (H then H again is identity) collapses back
    to the all-zero state -- no option activates. The picker must say so
    rather than inventing a winner."""
    import json
    spec_json = json.dumps({
        "options": ["open the door", "look out the window"],
        "phases": [0.0, 0.0],
        "entangled_pairs": [],
        "reflection": "a fork",
    })
    chosen, spec, result = zeev._resolve_dream_choice(
        "In a dream: a hallway forks. Two paths appear.",
        qllm=_fake_qllm(spec_json))
    assert chosen == "nothing — the moment dissolves"


def test_resolve_dream_choice_fails_closed_on_bad_spec(zeev):
    """A circuit-mapping failure must return all-None, never raise -- a
    failed choice has to fall through to an unforked dream, not abort it."""
    chosen, spec, result = zeev._resolve_dream_choice(
        "In a dream: a hallway forks.",
        qllm=lambda msgs, max_tokens=300, json_mode=False: (None, "network error"))
    assert (chosen, spec, result) == (None, None, None)


# --- structured, multi-beat composition -------------------------------------

def test_structured_dream_has_open_and_close_beats(zeev):
    """Two calls to the same llm (opening, then closing) around a choice
    point that fails closed (no qllm reaches the network) still produce a
    coherent two-beat dream."""
    calls = []

    def fake_llm(msgs):
        calls.append(msgs)
        if len(calls) == 1:
            return "I am standing at the edge of a hallway that forks."
        return ("The floor gives way and I am falling through paper.\n"
                "FRAGMENT: paper falling")

    d = zeev._compose_dream_structured(
        "zeev", "-", llm=fake_llm,
        qllm=lambda msgs, max_tokens=300, json_mode=False: (None, "no network in test"))
    assert len(calls) == 2
    assert "hallway that forks" in d["content"]
    assert "falling through paper" in d["content"]
    assert "FRAGMENT" not in d["content"]
    assert d["fragment"] == "paper falling"
    kinds = [b["kind"] for b in d["beats"]]
    assert kinds == ["open", "close"]        # no "choice" beat -- it failed closed


def test_structured_dream_records_a_resolved_choice_beat(zeev):
    """When the choice point succeeds, its beat carries the options and the
    winner, for save_dream_beats() to persist."""
    import json
    calls = []

    def fake_llm(msgs):
        calls.append(msgs)
        if len(calls) == 1:
            return "I am standing at the edge of a hallway that forks."
        return "I go through the door.\nFRAGMENT: a door"

    spec_json = json.dumps({
        "options": ["open the door", "look out the window"],
        "phases": [3.14159265, 0.0],
        "entangled_pairs": [],
        "reflection": "a fork",
    })
    d = zeev._compose_dream_structured(
        "zeev", "-", llm=fake_llm, qllm=_fake_qllm(spec_json))
    kinds = [b["kind"] for b in d["beats"]]
    assert kinds == ["open", "choice", "close"]
    choice_beat = d["beats"][1]
    assert choice_beat["chosen"] == "open the door"
    assert choice_beat["options"] == ["open the door", "look out the window"]


def test_structured_dream_survives_a_beat_two_failure(zeev):
    """If the closing beat's LLM call comes back empty, the dream is still
    the opening beat alone -- not nothing."""
    def fake_llm(msgs):
        if not hasattr(fake_llm, "calls"):
            fake_llm.calls = 0
        fake_llm.calls += 1
        if fake_llm.calls == 1:
            return "I am standing at the edge of a hallway that forks."
        return ""

    d = zeev._compose_dream_structured(
        "zeev", "-", llm=fake_llm,
        qllm=lambda msgs, max_tokens=300, json_mode=False: (None, "no network in test"))
    assert d["content"] == "I am standing at the edge of a hallway that forks."
    assert [b["kind"] for b in d["beats"]] == ["open"]


# --- persistence of the structured breakdown --------------------------------

def test_save_dream_returns_the_row_id(db):
    z = db
    dream_id = z.save_dream("zeev", "2026-08-10", "a library", "shelves", 0.9)
    assert isinstance(dream_id, int) and dream_id > 0
    assert z.save_dream("zeev", "2026-08-10", "another", "other", 0.9) is False


def test_save_dream_beats_round_trips(db):
    z = db
    dream_id = z.save_dream("zeev", "2026-08-11", "a hallway forks", "a fork", 0.8)
    beats = [
        {"seq": 1, "kind": "open", "text": "I stand at a fork."},
        {"seq": 2, "kind": "choice", "text": "idea", "options": ["door", "window"],
         "chosen": "door"},
        {"seq": 3, "kind": "close", "text": "I go through the door."},
    ]
    z.save_dream_beats(dream_id, beats)
    rows = z._db().execute(
        "SELECT seq, kind, text, options_json, chosen FROM dream_beats "
        "WHERE dream_id = ? ORDER BY seq", (dream_id,)).fetchall()
    assert [r["kind"] for r in rows] == ["open", "choice", "close"]
    import json
    assert json.loads(rows[1]["options_json"]) == ["door", "window"]
    assert rows[1]["chosen"] == "door"


def test_save_dream_beats_is_best_effort(db, monkeypatch):
    """A write failure here must never look like the dream itself failed to
    save -- dreams.content is already committed by the time this runs."""
    z = db
    dream_id = z.save_dream("zeev", "2026-08-12", "a library", "shelves", 0.9)

    def boom():
        raise RuntimeError("disk full")
    monkeypatch.setattr(z, "_db", boom)
    z.save_dream_beats(dream_id, [{"seq": 1, "kind": "open", "text": "x"}])  # must not raise


def test_dream_once_persists_beats(db, monkeypatch):
    """End to end: a successful dream_once() call leaves rows in both tables."""
    z = db
    monkeypatch.setattr(z, "_dream_material", lambda limit=25: "- a quiet day")
    monkeypatch.setattr(
        z, "_compose_dream_structured",
        lambda persona, material, llm=None, qllm=None: {
            "content": "a hallway forks and I go through the door",
            "fragment": "a door",
            "beats": [
                {"seq": 1, "kind": "open", "text": "a hallway forks"},
                {"seq": 2, "kind": "choice", "text": "idea", "options": ["door", "window"],
                 "chosen": "door"},
                {"seq": 3, "kind": "close", "text": "I go through the door"},
            ],
        })

    class FixedVivid:
        def random(self):
            return 0.01     # below _DREAM_CHANCE threshold -> dreams tonight
    outcome = z.dream_once("zeev", rng=FixedVivid())
    assert outcome == "dreamt"
    dream_row = z._db().execute(
        "SELECT id, content FROM dreams WHERE persona = 'zeev'").fetchone()
    assert dream_row["content"] == "a hallway forks and I go through the door"
    beat_rows = z._db().execute(
        "SELECT kind FROM dream_beats WHERE dream_id = ? ORDER BY seq",
        (dream_row["id"],)).fetchall()
    assert [r["kind"] for r in beat_rows] == ["open", "choice", "close"]
