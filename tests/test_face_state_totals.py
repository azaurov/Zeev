"""Per-state cumulative time totals, persisted so ragnarok's standalone
battery_log.py (a separate process, on its own 5-min systemd timer) can tell
real idle time from real active time when correlating against battery drain.

_record_state_transition is called from _set_face inside run_device_mode,
which needs the HAT to import -- so, same reasoning as handle_transcript/
finish_turn, the actual bookkeeping is pulled out to module level here.
"""
NOW = 1_000_000.0


def test_load_missing_file_returns_empty(zeev, tmp_path):
    totals, cur_state, cur_since = zeev._load_state_totals(tmp_path / "nope.json")
    assert totals == {}
    assert cur_state is None
    assert cur_since is None


def test_first_transition_has_nothing_to_roll_up(zeev, tmp_path):
    """No prior current_state -- there's no elapsed duration to bank yet,
    just a starting point for the next transition."""
    path = tmp_path / "totals.json"
    zeev._record_state_transition("idle", now=NOW, path=path)
    totals, cur_state, cur_since = zeev._load_state_totals(path)
    assert totals == {}
    assert cur_state == "idle"
    assert cur_since == NOW


def test_transition_rolls_elapsed_time_into_previous_state(zeev, tmp_path):
    path = tmp_path / "totals.json"
    zeev._record_state_transition("idle", now=NOW, path=path)
    zeev._record_state_transition("listening", now=NOW + 5, path=path)
    zeev._record_state_transition("speaking", now=NOW + 5 + 12, path=path)

    totals, cur_state, cur_since = zeev._load_state_totals(path)
    assert totals["idle"] == 5
    assert totals["listening"] == 12
    assert "speaking" not in totals  # still ongoing, not rolled up yet
    assert cur_state == "speaking"
    assert cur_since == NOW + 17


def test_revisiting_a_state_accumulates_rather_than_overwrites(zeev, tmp_path):
    path = tmp_path / "totals.json"
    zeev._record_state_transition("idle", now=NOW, path=path)
    zeev._record_state_transition("listening", now=NOW + 10, path=path)
    zeev._record_state_transition("idle", now=NOW + 13, path=path)
    zeev._record_state_transition("listening", now=NOW + 20, path=path)

    totals, _, _ = zeev._load_state_totals(path)
    assert totals["idle"] == 10 + 7  # two separate idle stretches, summed
    assert totals["listening"] == 3


def test_write_is_atomic_no_leftover_tmp_file(zeev, tmp_path):
    path = tmp_path / "totals.json"
    zeev._write_state_totals({"idle": 1.0}, "idle", NOW, path=path)
    leftovers = list(tmp_path.glob("*.tmp*"))
    assert leftovers == []
    assert path.exists()


def test_write_failure_does_not_raise(zeev, tmp_path):
    """A totals write must never be able to break a live device turn --
    point it at a directory that can't exist (parent is a file, not a dir)
    and confirm this stays silent rather than propagating."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    bad_path = blocker / "totals.json"
    zeev._write_state_totals({"idle": 1.0}, "idle", NOW, path=bad_path)  # no raise


def test_record_transition_swallows_a_load_failure(zeev, tmp_path, monkeypatch):
    path = tmp_path / "totals.json"

    def _boom(*a, **kw):
        raise RuntimeError("disk hiccup")

    monkeypatch.setattr(zeev, "_load_state_totals", _boom)
    zeev._record_state_transition("idle", now=NOW, path=path)  # no raise
