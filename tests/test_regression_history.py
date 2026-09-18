"""Regressions for bugs this project actually shipped, fixed, and could ship again.

Every test here corresponds to a real incident, named in its docstring with the
date and the commit that fixed it. The common thread is SILENT failure: in each
case nothing raised, nothing logged an error, and the system reported success
while doing nothing useful. That is the class of bug a normal test suite misses,
because "no exception" was always true.

So these assert that something ACTUALLY HAPPENS -- output is emitted, a failure
is propagated as False, a guard is still present -- rather than that a call
returned without blowing up.

Some of these are structural (they read the source or a script's text) rather
than behavioural. That is deliberate: a print trapped inside a branch and a
deleted safety check in a deploy script cannot be caught by calling a function,
and a cheap structural assertion that fails loudly beats no coverage at all.
"""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ZEEV_PY = REPO / "zeev" / "zeev.py"
DEPLOY_SH = REPO / "deploy.sh"


# --------------------------------------------------------------------------
# 2026-09-17, commit 544dbc6 -- "Make the energy-gate summary unskippable"
#
# The wake listener's periodic "[wake] energy gate: scored N/M frames" summary
# sat inside the gate's if/elif/else, under the below-threshold branch. Frames
# during speech or a post-onset hold took a different branch, so if the
# every-Nth-frame counter landed there, the summary was simply never printed --
# no error, no output, and the only window into how much audio the gate was
# skipping went dark. Fixed by making it time-based AND moving it ABOVE the
# branching, so every frame reaches it.
# --------------------------------------------------------------------------
def _wake_loop_region():
    """The text of _wake_loop_oww, sliced out of zeev.py.

    It is a closure inside run_device_mode (which needs the HAT to import), so
    it cannot be imported and inspected directly on a dev box.
    """
    src = ZEEV_PY.read_text()
    start = src.index("    def _wake_loop_oww(model, label):")
    end = src.index("    def _wake_loop_cloud():", start)
    return src[start:end]


def test_energy_gate_summary_is_not_trapped_inside_a_branch():
    region = _wake_loop_region()
    summary = region.index('"[wake] energy gate: scored ')
    hold_branch = region.index("if hold > 0:")
    assert summary < hold_branch, (
        "The energy-gate summary print moved back below the gate's branching. "
        "It must stay above `if hold > 0:` so every frame reaches it -- when it "
        "sat inside the else branch (544dbc6, 2026-09-17) the summary silently "
        "stopped appearing whenever the counter landed mid-speech."
    )


def test_energy_gate_summary_is_time_based_not_frame_modulo():
    """A frame-modulo trigger is skippable by construction; elapsed time is not."""
    region = _wake_loop_region()
    head = region[:region.index('"[wake] energy gate: scored ')]
    assert "time.time() - last_gate_log >= OWW_GATE_LOG_SEC" in head, (
        "The gate summary must fire on elapsed time. A `seen % N == 0` style "
        "condition can be skipped by the very frames it is meant to report on."
    )


# --------------------------------------------------------------------------
# 2026-09-12, commits 47464b3 (shipped) -> 4a9d66a (reverted) -> 4050dda (fixed)
#
# An optimization made c11_gv_dial return as soon as the CALL intent was fired,
# confirming Google Voice's call setup in a background thread so bt_call_loop
# could start listening sooner. It cut latency and it also meant the phone never
# rang: dial() reported success, the loop listened to a call that was never
# placed, and nothing errored. The optimization was reverted wholesale.
#
# The invariant that replaced it: dial reports the REAL outcome of waiting for
# GV to reach the calling state. It must never return True on "intent fired".
#
# Note for whoever touches this next: the reverted commit shipped WITH a test
# that asserted the optimistic behaviour. A regression test encodes an
# invariant, so it is only as good as the invariant -- this one is pinned to
# "the call actually rang", which is the thing the user experiences.
# --------------------------------------------------------------------------
def test_dial_reports_failure_when_the_call_never_reaches_calling(zeev, monkeypatch):
    calls = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr(zeev, "c11_wake_unlock", lambda *a, **k: None)
    monkeypatch.setattr(zeev, "_c11_ensure_gv_warm", lambda *a, **k: True)
    monkeypatch.setattr(zeev.subprocess, "run", lambda *a, **k: _Result())
    # GV never reaches the call screen -- exactly the live failure.
    monkeypatch.setattr(zeev, "_c11_hold_gv_foreground_until_calling",
                        lambda *a, **k: calls.setdefault("waited", True) is None or False)

    assert zeev.c11_gv_dial("5551234567") is False, (
        "c11_gv_dial returned success although GV never reached the calling "
        "state. bt_call_loop would then listen to a call that was never placed "
        "-- the phone simply never rings and nothing reports an error."
    )
    assert calls.get("waited"), "dial must actually wait for the calling state"


def test_dial_waits_for_calling_state_before_returning_true(zeev, monkeypatch):
    """The happy path must ALSO go through the wait, not skip it."""
    seen = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr(zeev, "c11_wake_unlock", lambda *a, **k: None)
    monkeypatch.setattr(zeev, "_c11_ensure_gv_warm", lambda *a, **k: True)
    monkeypatch.setattr(zeev.subprocess, "run", lambda *a, **k: _Result())

    def _held(*a, **k):
        seen["held"] = True
        return True

    monkeypatch.setattr(zeev, "_c11_hold_gv_foreground_until_calling", _held)
    assert zeev.c11_gv_dial("5551234567") is True
    assert seen.get("held"), (
        "dial returned True without waiting for the calling state -- this is "
        "precisely the reverted 47464b3 optimization coming back."
    )


# --------------------------------------------------------------------------
# deploy.sh's own safety gates. These are not hypothetical: the script exists
# because a manual ssh-and-restart deploy skips the test suite, can restart the
# Pi against a stale checkout, and reports health from `systemctl is-active`,
# which goes green the instant the process execs -- well before the display,
# audio and wake listener are up. Each guard below has cost real debugging once.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("needle,why", [
    ("HEAD verified",
     "the post-pull HEAD assertion -- without it the Pi can be restarted "
     "against a stale checkout and the deploy still reports success"),
    ("journalctl",
     "the startup-banner health poll -- `systemctl is-active` reports healthy "
     "before the display/audio/wake listener exist"),
    ("Traceback",
     "the post-restart traceback check -- a crashing service can print its "
     "banner first and still be broken"),
])
def test_deploy_script_keeps_its_guards(needle, why):
    assert DEPLOY_SH.exists(), "deploy.sh is the only sanctioned path to the Pi"
    text = DEPLOY_SH.read_text()
    assert needle in text, f"deploy.sh lost {why}"


def test_deploy_script_runs_the_suite_before_shipping():
    text = DEPLOY_SH.read_text()
    assert "pytest" in text, (
        "deploy.sh must run the test suite before deploying. A harness nothing "
        "runs is decoration -- this is the line that makes every other test in "
        "this directory load-bearing."
    )
