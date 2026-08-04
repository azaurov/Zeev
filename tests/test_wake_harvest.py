"""Active learning from wake-word false-positive logs (docs/research-directions.md #4).

parse_wake_triggers, review_triggers, load_negatives and save_negatives are
the pure/injectable logic behind zeev/wake_harvest.py, the CLI that turns
journalctl into hard negatives for the next openWakeWord retraining round
(docs/wake-word-training.md's custom_negative_strings). All testable here
without real journalctl or stdin.
"""
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def wh():
    """wake_harvest.py lives in zeev/ and is a standalone script (no `from
    zeev import ...`), so this just needs it on sys.path -- same trick
    tests/test_wake_enrollment.py uses for wake_enroll.py."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "zeev"))
    import wake_harvest as _wh
    return _wh


def _line(model="hey_zeev", score="0.73"):
    return f"Aug 04 10:15:22 ragnarok zeev-device[1234]: [wake] {model} trigger (score {score})"


# ---------------------------------------------------------------------------
# parse_wake_triggers
# ---------------------------------------------------------------------------

def test_pairs_trigger_with_the_following_you_line(wh):
    lines = [_line(), "Aug 04 10:15:23 ragnarok zeev-device[1234]: You: what's the weather"]
    triggers, dropped = wh.parse_wake_triggers(lines)
    assert dropped == 0
    assert triggers == [{"model": "hey_zeev", "score": 0.73, "transcript": "what's the weather"}]


def test_pairs_trigger_with_a_discarded_noise_transcript(wh):
    lines = [
        _line(model="hey_sarina", score="0.55"),
        "Aug 04 10:15:24 ragnarok zeev-device[1234]: [wake] discarded noise transcript: 'Thank you.'",
    ]
    triggers, dropped = wh.parse_wake_triggers(lines)
    assert dropped == 0
    assert triggers == [{"model": "hey_sarina", "score": 0.55, "transcript": "Thank you."}]


def test_noise_transcript_repr_with_apostrophe_is_parsed_correctly(wh):
    """The real log line uses Python's !r (repr): a transcript containing an
    apostrophe makes repr switch to double quotes, which a naive quote-strip
    breaks on but ast.literal_eval handles."""
    lines = [_line(), '''Aug 04 10:15:24 ragnarok zeev-device[1234]: [wake] discarded noise transcript: "it's raining"''']
    triggers, dropped = wh.parse_wake_triggers(lines)
    assert triggers[0]["transcript"] == "it's raining"


def test_ansi_codes_are_stripped(wh):
    lines = [_line(), "\x1b[1mYou:\x1b[0m \x1b[2m[voice]\x1b[0m turn the volume up"]
    triggers, _ = wh.parse_wake_triggers(lines)
    assert triggers[0]["transcript"] == "turn the volume up"


def test_second_trigger_before_a_transcript_drops_the_first(wh):
    lines = [
        _line(model="hey_zeev", score="0.60"),
        _line(model="hey_sarina", score="0.90"),
        "Aug 04 10:15:25 ragnarok zeev-device[1234]: You: play some jazz",
    ]
    triggers, dropped = wh.parse_wake_triggers(lines)
    assert dropped == 1
    assert len(triggers) == 1
    assert triggers[0]["model"] == "hey_sarina"


def test_trigger_with_no_transcript_at_end_of_log_is_dropped_and_counted(wh):
    lines = [_line()]
    triggers, dropped = wh.parse_wake_triggers(lines)
    assert triggers == []
    assert dropped == 1


def test_trigger_past_max_lookahead_without_a_transcript_is_dropped(wh):
    lines = [_line()] + ["Aug 04 10:15:2%d ragnarok zeev-device[1234]: [reminders] tick" % (i % 10)
                          for i in range(wh._MAX_LOOKAHEAD + 5)]
    triggers, dropped = wh.parse_wake_triggers(lines)
    assert triggers == []
    assert dropped == 1


def test_unrelated_lines_between_trigger_and_transcript_are_skipped(wh):
    """Interleaving from another thread (reminders, gcal) must not break
    pairing as long as it's within the lookahead window."""
    lines = [
        _line(),
        "Aug 04 10:15:23 ragnarok zeev-device[1234]: [gcal] auto-reminders: +0 moved=0 pruned=0",
        "Aug 04 10:15:24 ragnarok zeev-device[1234]: You: set a timer for five minutes",
    ]
    triggers, dropped = wh.parse_wake_triggers(lines)
    assert dropped == 0
    assert triggers[0]["transcript"] == "set a timer for five minutes"


def test_no_lines_yields_nothing(wh):
    assert wh.parse_wake_triggers([]) == ([], 0)


def test_lines_with_no_trigger_are_ignored(wh):
    triggers, dropped = wh.parse_wake_triggers(["some unrelated log line", "You: orphaned transcript"])
    assert triggers == []
    assert dropped == 0


# ---------------------------------------------------------------------------
# load_negatives / save_negatives
# ---------------------------------------------------------------------------

def test_load_negatives_missing_file_is_empty_list(wh, tmp_path):
    assert wh.load_negatives(tmp_path / "nope.json") == []


def test_load_negatives_malformed_json_warns_and_returns_empty(wh, tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    assert wh.load_negatives(p) == []
    assert "not valid JSON" in capsys.readouterr().err


def test_save_then_load_roundtrips(wh, tmp_path):
    p = tmp_path / "out" / "negatives.json"
    wh.save_negatives(p, ["thank you", "goodbye"])
    assert wh.load_negatives(p) == ["thank you", "goodbye"]


# ---------------------------------------------------------------------------
# review_triggers
# ---------------------------------------------------------------------------

def test_review_collects_only_y_answers(wh):
    triggers = [
        {"model": "hey_zeev", "score": 0.6, "transcript": "thank you"},
        {"model": "hey_zeev", "score": 0.9, "transcript": "hey ze'ev what time is it"},
        {"model": "hey_zeev", "score": 0.55, "transcript": "goodbye"},
    ]
    answers = iter(["y", "n", "y"])
    result = wh.review_triggers(triggers, existing=[], ask=lambda _: next(answers))
    assert result == ["thank you", "goodbye"]


def test_review_q_stops_early(wh):
    triggers = [
        {"model": "hey_zeev", "score": 0.6, "transcript": "one"},
        {"model": "hey_zeev", "score": 0.6, "transcript": "two"},
    ]
    answers = iter(["q"])
    result = wh.review_triggers(triggers, existing=[], ask=lambda _: next(answers))
    assert result == []


def test_review_skips_transcripts_already_saved(wh):
    triggers = [{"model": "hey_zeev", "score": 0.6, "transcript": "thank you"}]

    def _boom(_):
        raise AssertionError("should not ask about an already-saved negative")

    result = wh.review_triggers(triggers, existing=["thank you"], ask=_boom)
    assert result == []


def test_review_skips_duplicate_transcripts_within_the_same_run(wh):
    """The same false wake often recurs in one log window (a TV ad plays
    more than once) -- must only be asked about once."""
    triggers = [
        {"model": "hey_zeev", "score": 0.6, "transcript": "thank you"},
        {"model": "hey_zeev", "score": 0.65, "transcript": "thank you"},
    ]
    asked = []

    def _ask(prompt):
        asked.append(prompt)
        return "y"

    result = wh.review_triggers(triggers, existing=[], ask=_ask)
    assert len(asked) == 1
    assert result == ["thank you"]


def test_review_skips_empty_transcripts(wh):
    triggers = [{"model": "hey_zeev", "score": 0.6, "transcript": ""}]

    def _boom(_):
        raise AssertionError("should not ask about an empty transcript")

    assert wh.review_triggers(triggers, existing=[], ask=_boom) == []
