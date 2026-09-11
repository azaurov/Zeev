"""Google Voice calling backend (gv_call.py) + the bt_call_loop backend
abstraction that lets it plug into the existing calling logic.

Live-verified 2026-09-11 (see project memory `gv_vm_calling_investigation.md`):
three real Google Voice calls placed via voice.google.com in a Chrome
instance on bosgame, confirmed end-to-end including a real Zeev TTS line
heard "loud and clear" by a human on the far end. This test file pins the
pure-logic pieces only -- no live CDP/Chrome/Google network calls, same
reasoning as the rest of this suite mocking out real TTS/network calls.

The DOM-manipulation helpers inside GVSession (the native-setter React
input trick, the state()/dial() CSS selectors) are CDP-only/integration-only
-- there's no DOM/JS test runner in this suite to exercise them against,
and faking one would test the fake, not the real page. They're covered by
the live verification above instead.
"""
import subprocess

import pytest


@pytest.fixture
def gv_call(zeev):
    """Import gv_call.py the same way zeev.py itself does (zeev/ is
    already on sys.path via the `zeev` fixture)."""
    import gv_call as _gv_call
    return _gv_call


# ---------------------------------------------------------------------------
# Backend interface conformance -- SCOCallIO and GVCallIO must expose the
# same method set, since bt_call_loop only ever calls call_io.<method>()
# without knowing which backend it got.
# ---------------------------------------------------------------------------

REQUIRED_CALL_IO_METHODS = {
    "speak", "capture_popen", "fast_detect", "is_hungup", "hangup",
    "send_dtmf", "play_wav",
}


def test_sco_call_io_has_the_required_methods(zeev):
    missing = REQUIRED_CALL_IO_METHODS - set(dir(zeev.SCOCallIO))
    assert not missing, f"SCOCallIO is missing: {missing}"


def test_gv_call_io_has_the_required_methods(gv_call):
    missing = REQUIRED_CALL_IO_METHODS - set(dir(gv_call.GVCallIO))
    assert not missing, f"GVCallIO is missing: {missing}"


def test_gv_call_io_has_a_samplerate_attribute(gv_call):
    """bt_call_loop reads call_io.samplerate directly (for bt_call_record_wav
    and the fast_detect/capture_popen wiring) -- both backends must have it."""
    assert isinstance(gv_call.GVCallIO.samplerate, int)


# ---------------------------------------------------------------------------
# ensure_virtual_sinks() -- must refuse to proceed rather than silently let
# Chrome fall back to the default sink (the real yard Bluetooth speaker on
# bosgame). See its own docstring for the full reasoning.
# ---------------------------------------------------------------------------

def _fake_run(sink_names, **_):
    class _Result:
        stdout = "\n".join(f"999\t{n}\tPipeWire\tSUSPENDED" for n in sink_names)
    return _Result()


def test_ensure_virtual_sinks_raises_when_absent(gv_call, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(["some_other_sink"]))
    with pytest.raises(gv_call.GVCallError):
        gv_call.ensure_virtual_sinks(timeout=1)


def test_ensure_virtual_sinks_passes_when_present(gv_call, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_run([gv_call.PULSE_SINK, gv_call.MIC_FEED_SINK]),
    )
    gv_call.ensure_virtual_sinks(timeout=1)  # must not raise


# ---------------------------------------------------------------------------
# _display_env() -- must fail loudly (not silently launch Chrome with no
# DISPLAY) when bosgame's GNOME session isn't up.
# ---------------------------------------------------------------------------

def test_display_env_raises_without_xauth_files(gv_call, monkeypatch):
    monkeypatch.setattr("os.listdir", lambda path: [])
    with pytest.raises(gv_call.GVCallError):
        gv_call._display_env()


# ---------------------------------------------------------------------------
# GVCallIO.send_dtmf() -- deliberately unsupported for now (see plan doc);
# must degrade gracefully, never crash or silently pretend it worked.
# ---------------------------------------------------------------------------

def test_send_dtmf_returns_false_not_crash(gv_call):
    io = gv_call.GVCallIO.__new__(gv_call.GVCallIO)  # no real GVSession needed
    assert io.send_dtmf("5") is False


def test_gv_call_io_takes_speak_sco_and_vad_collect_as_params(gv_call):
    """Must NOT import zeev.py itself internally -- see GVCallIO's own
    docstring: zeev.py runs as __main__ in production, so a plain `import
    zeev` from here would silently re-execute the whole file a second time
    rather than reuse the one already running. These are passed in instead."""
    import inspect
    params = list(inspect.signature(gv_call.GVCallIO.__init__).parameters)
    assert params == ["self", "session", "speak_sco_fn", "vad_collect_fn"]


# ---------------------------------------------------------------------------
# bt_speak_sco's play_cmd override -- the one change to existing SCO code,
# must default to the original aplay behavior when not given.
# ---------------------------------------------------------------------------

def test_bt_speak_sco_defaults_play_cmd_to_none(zeev):
    import inspect
    sig = inspect.signature(zeev.bt_speak_sco)
    assert sig.parameters["play_cmd"].default is None
