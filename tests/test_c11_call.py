"""C11-over-Bluetooth Google Voice calling backend (--via c11).

C11 (a dedicated WiFi-only, no-SIM Android device) places the call itself
through its own Google Voice app, driven via adb; the audio side reuses
SCOCallIO completely unchanged -- the same hardware-reliable SCO/HFP path
the "sco" backend already uses in production, since Android's Bluetooth
HFP AG stack bridges any real Telecom call, regardless of who placed it.

Live-verified 2026-09-11 (see project memory `gv_vm_calling_investigation.md`):
two real calls placed via C11 after fixing a crashed Bluetooth HAL there,
both confirmed bidirectionally -- real captured speech and a real Zeev TTS
line the user confirmed hearing "loud and clear."

This file pins the pure-logic pieces only (number formatting, missing-env
handling) -- no live adb/Bluetooth calls, same reasoning as test_gv_call.py.
"""
import subprocess

import pytest


def test_c11_gv_dial_requires_adb_serial(zeev, monkeypatch):
    monkeypatch.delenv("C11_ADB_SERIAL", raising=False)
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "")
    assert zeev.c11_gv_dial("5551234567") is False


def test_c11_gv_dial_rejects_no_digits(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr(zeev, "c11_wake_unlock", lambda s: None)
    monkeypatch.setattr(zeev, "_c11_ensure_gv_warm", lambda s, **kw: True)
    assert zeev.c11_gv_dial("not a number") is False


def test_c11_gv_dial_formats_ten_digit_number_with_country_code(zeev, monkeypatch):
    """Found live: GV's ACTION_CALL intent needs the full +1 country code --
    a plain 10-digit tel: URI gets rejected."""
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr(zeev, "c11_wake_unlock", lambda s: None)
    monkeypatch.setattr(zeev, "_c11_ensure_gv_warm", lambda s, **kw: True)
    captured = {}

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert zeev.c11_gv_dial("5081234567") is True
    assert "tel:+15081234567" in captured["cmd"]


def test_c11_gv_dial_leaves_a_leading_plus_number_untouched(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr(zeev, "c11_wake_unlock", lambda s: None)
    monkeypatch.setattr(zeev, "_c11_ensure_gv_warm", lambda s, **kw: True)
    captured = {}

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert zeev.c11_gv_dial("+442071234567") is True
    assert "tel:+442071234567" in captured["cmd"]


def test_c11_gv_dial_targets_the_right_activity_and_serial(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "9.9.9.9:1234")
    monkeypatch.setattr(zeev, "c11_wake_unlock", lambda s: None)
    monkeypatch.setattr(zeev, "_c11_ensure_gv_warm", lambda s, **kw: True)
    captured = {}

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    zeev.c11_gv_dial("5081234567")
    assert captured["cmd"][:3] == ["adb", "-s", "9.9.9.9:1234"]
    assert zeev.C11_GV_CALL_ACTIVITY in captured["cmd"]


def test_c11_gv_dial_returns_false_on_adb_error(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr(zeev, "c11_wake_unlock", lambda s: None)
    monkeypatch.setattr(zeev, "_c11_ensure_gv_warm", lambda s, **kw: True)

    class _FakeResult:
        returncode = 1
        stdout = ""
        stderr = "error: device offline"

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: _FakeResult())
    assert zeev.c11_gv_dial("5081234567") is False


def test_c11_gv_dial_returns_false_on_exception(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr(zeev, "c11_wake_unlock", lambda s: None)
    monkeypatch.setattr(zeev, "_c11_ensure_gv_warm", lambda s, **kw: True)

    def _raise(cmd, **kwargs):
        raise FileNotFoundError("adb not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert zeev.c11_gv_dial("5081234567") is False


def test_c11_bt_mac_has_a_sensible_default(zeev):
    assert zeev.C11_BT_MAC.count(":") == 5  # a MAC address shape


def test_c11_gv_dial_wakes_and_unlocks_before_dialing(zeev, monkeypatch):
    """Found live: C11 dozes/locks between calls. c11_wake_unlock() must
    run before the CALL intent, not after or not at all."""
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    calls = []

    def _fake_wake_unlock(serial):
        calls.append(("wake_unlock", serial))

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append(("run", cmd))
        return _FakeResult()

    monkeypatch.setattr(zeev, "c11_wake_unlock", _fake_wake_unlock)
    monkeypatch.setattr(zeev, "_c11_ensure_gv_warm", lambda s, **kw: calls.append(("warm", s)) or True)
    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert zeev.c11_gv_dial("5081234567") is True
    assert calls[0] == ("wake_unlock", "1.2.3.4:5555")
    assert calls[1] == ("warm", "1.2.3.4:5555")


def test_c11_gv_dial_ensures_gv_warm_before_dialing(zeev, monkeypatch):
    """Found live 2026-09-11: a cold GV process silently swallows the CALL
    intent (app was still doing its own account-ready bootstrap), so no
    call ever rang. _c11_ensure_gv_warm() must run after wake_unlock and
    before the actual CALL intent, not skipped."""
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr(zeev, "c11_wake_unlock", lambda s: None)
    calls = []
    monkeypatch.setattr(zeev, "_c11_ensure_gv_warm", lambda s, **kw: calls.append(s) or True)

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: _FakeResult())
    assert zeev.c11_gv_dial("5081234567") is True
    assert calls == ["1.2.3.4:5555"]


def test_c11_ensure_gv_warm_launches_and_waits_for_resumed(zeev, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "dumpsys" in cmd:
            r = _FakeResult()
            r.stdout = zeev.C11_GV_PKG + " some other text"
            return r
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert zeev._c11_ensure_gv_warm("1.2.3.4:5555") is True
    assert ["adb", "-s", "1.2.3.4:5555", "shell", "monkey", "-p", zeev.C11_GV_PKG,
            "-c", "android.intent.category.LAUNCHER", "1"] in calls


def test_c11_ensure_gv_warm_times_out_and_returns_false(zeev, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    class _FakeResult:
        returncode = 0
        stdout = ""  # GV package never appears -- never becomes resumed
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: _FakeResult())
    assert zeev._c11_ensure_gv_warm("1.2.3.4:5555", timeout=0.01) is False


def test_c11_ensure_gv_warm_is_best_effort_on_launch_exception(zeev, monkeypatch):
    def _raise(cmd, **kwargs):
        raise FileNotFoundError("adb not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert zeev._c11_ensure_gv_warm("1.2.3.4:5555") is False  # must not raise


def test_c11_wake_unlock_sends_wakeup_and_dismiss_keyguard(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_lock_pin", lambda: "")
    captured = []

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    zeev.c11_wake_unlock("1.2.3.4:5555")
    assert ["adb", "-s", "1.2.3.4:5555", "shell", "input", "keyevent", "KEYCODE_WAKEUP"] in captured
    assert ["adb", "-s", "1.2.3.4:5555", "shell", "wm", "dismiss-keyguard"] in captured


def test_c11_wake_unlock_enters_pin_when_set(zeev, monkeypatch):
    """Found live 2026-09-11: C11 has a real secured PIN lock, so
    wm dismiss-keyguard alone cannot get past it -- the PIN must be
    swiped-to + typed + confirmed."""
    monkeypatch.setattr(zeev, "c11_lock_pin", lambda: "1441")
    captured = []

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    zeev.c11_wake_unlock("1.2.3.4:5555")
    assert ["adb", "-s", "1.2.3.4:5555", "shell", "input", "swipe",
            "540", "1800", "540", "1000"] in captured
    assert ["adb", "-s", "1.2.3.4:5555", "shell", "input", "text", "1441"] in captured
    assert ["adb", "-s", "1.2.3.4:5555", "shell", "input", "keyevent", "KEYCODE_ENTER"] in captured


def test_c11_lock_pin_reads_from_env_only(zeev, monkeypatch):
    monkeypatch.delenv("C11_LOCK_PIN", raising=False)
    assert zeev.c11_lock_pin() == ""
    monkeypatch.setenv("C11_LOCK_PIN", "1441")
    assert zeev.c11_lock_pin() == "1441"


def test_c11_wake_unlock_is_best_effort_on_exception(zeev, monkeypatch):
    def _raise(cmd, **kwargs):
        raise FileNotFoundError("adb not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    zeev.c11_wake_unlock("1.2.3.4:5555")  # must not raise


# ---------------------------------------------------------------------------
# c11_gv_hangup() / C11CallIO -- two real bugs found live 2026-09-11:
#
# 1. AT+CHUP is accepted by C11's Bluetooth stack but does NOT actually end
#    a Google Voice SelfManaged call (confirmed live: sent AT+CHUP, got
#    "OK", the call was still visibly active moments later). Fixed by
#    switching to KEYCODE_ENDCALL.
#
# 2. On a genuinely longer live call, KEYCODE_ENDCALL itself was sent TWICE
#    (both accepted by adbd, confirmed in logcat) and the call stayed live
#    for minutes afterward (confirmed via screenshot, timer still
#    counting) -- a direct UI tap on the actual hang-up button was the only
#    thing that reliably worked. c11_gv_hangup() now verifies via
#    _c11_call_activity_resumed() (VoipCallActivity -> CallSurveyActivity,
#    a confirmed-reliable transition), retries the keyevent once, and
#    falls back to a uiautomator-located tap if the call is still live.
# ---------------------------------------------------------------------------

def test_c11_gv_hangup_requires_adb_serial(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "")
    assert zeev.c11_gv_hangup() is False


def test_c11_gv_hangup_succeeds_on_first_endcall(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(zeev, "_c11_send_endcall", lambda s: calls.append("send") or True)
    monkeypatch.setattr(zeev, "_c11_call_activity_resumed", lambda s: calls.append("check") or False)
    tap_calls = []
    monkeypatch.setattr(zeev, "_c11_tap_hangup_button", lambda s: tap_calls.append(True) or True)
    assert zeev.c11_gv_hangup() is True
    assert calls == ["send", "check"]
    assert tap_calls == []


def test_c11_gv_hangup_retries_endcall_once_before_tap(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr("time.sleep", lambda s: None)
    send_calls = []
    monkeypatch.setattr(zeev, "_c11_send_endcall", lambda s: send_calls.append(1) or True)
    resumed_results = iter([True, False])  # still active, then ended after retry
    monkeypatch.setattr(zeev, "_c11_call_activity_resumed", lambda s: next(resumed_results))
    tap_calls = []
    monkeypatch.setattr(zeev, "_c11_tap_hangup_button", lambda s: tap_calls.append(True) or True)
    assert zeev.c11_gv_hangup() is True
    assert len(send_calls) == 2
    assert tap_calls == []


def test_c11_gv_hangup_falls_back_to_ui_tap(zeev, monkeypatch):
    """The real live scenario: two KEYCODE_ENDCALL attempts both failed to
    end the call, so the tap fallback must fire and, if it works, the
    overall result must be True."""
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(zeev, "_c11_send_endcall", lambda s: True)
    resumed_results = iter([True, True, False])  # still active x2, ended after tap
    monkeypatch.setattr(zeev, "_c11_call_activity_resumed", lambda s: next(resumed_results))
    tap_calls = []
    monkeypatch.setattr(zeev, "_c11_tap_hangup_button", lambda s: tap_calls.append(True) or True)
    assert zeev.c11_gv_hangup() is True
    assert tap_calls == [True]


def test_c11_gv_hangup_treats_tap_not_finding_a_button_as_success_if_call_already_ended(zeev, monkeypatch):
    """Found live 2026-09-11: bt_call_loop's own exit-path hangup and
    run_call_mode's finally-block hangup (or a SIGTERM handler racing a
    natural loop exit) can both fire close together. If the first
    KEYCODE_ENDCALL genuinely worked, just slower than the 1.5s check
    window, this call site's tap fallback finds no button because the
    call has already ended -- that must resolve to True, not a false
    'all hangup attempts failed' alarm."""
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(zeev, "_c11_send_endcall", lambda s: True)
    resumed_results = iter([True, True, False])  # still active x2, ended by the time of the final check
    monkeypatch.setattr(zeev, "_c11_call_activity_resumed", lambda s: next(resumed_results))
    monkeypatch.setattr(zeev, "_c11_tap_hangup_button", lambda s: False)  # no button found
    assert zeev.c11_gv_hangup() is True


def test_c11_gv_hangup_gives_up_and_returns_false_if_nothing_works(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(zeev, "_c11_send_endcall", lambda s: True)
    monkeypatch.setattr(zeev, "_c11_call_activity_resumed", lambda s: True)  # never ends
    monkeypatch.setattr(zeev, "_c11_tap_hangup_button", lambda s: True)
    assert zeev.c11_gv_hangup() is False


def test_c11_send_endcall_sends_the_right_keyevent(zeev, monkeypatch):
    captured = {}

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert zeev._c11_send_endcall("1.2.3.4:5555") is True
    assert captured["cmd"] == ["adb", "-s", "1.2.3.4:5555", "shell",
                                "input", "keyevent", "KEYCODE_ENDCALL"]


def test_c11_send_endcall_returns_false_on_exception(zeev, monkeypatch):
    def _raise(cmd, **kwargs):
        raise FileNotFoundError("adb not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert zeev._c11_send_endcall("1.2.3.4:5555") is False


def test_c11_call_activity_resumed_true_when_voip_activity_is_resumed(zeev, monkeypatch):
    class _FakeResult:
        returncode = 0
        stdout = "topResumedActivity=... com.google.android.apps.googlevoice/com.google.android.apps.voice.voip.ui.VoipCallActivity ..."
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: _FakeResult())
    assert zeev._c11_call_activity_resumed("1.2.3.4:5555") is True


def test_c11_call_activity_resumed_false_once_call_survey_activity_shows(zeev, monkeypatch):
    """Found live: the moment a call really ends, C11 transitions from
    VoipCallActivity to CallSurveyActivity (the post-call rating screen)
    -- confirmed to be a reliable end-of-call signal, unlike mCalls."""
    class _FakeResult:
        returncode = 0
        stdout = "topResumedActivity=... com.google.android.apps.googlevoice/com.google.android.apps.voice.voip.ui.callsurvey.CallSurveyActivity ..."
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: _FakeResult())
    assert zeev._c11_call_activity_resumed("1.2.3.4:5555") is False


def test_c11_call_activity_resumed_fails_toward_true_on_exception(zeev, monkeypatch):
    """A spurious extra hangup attempt is harmless; a missed one leaves a
    live mic open -- so an adb error must be treated as 'still active,
    keep trying', not 'must be fine.'"""
    def _raise(cmd, **kwargs):
        raise FileNotFoundError("adb not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert zeev._c11_call_activity_resumed("1.2.3.4:5555") is True


_FAKE_UI_DUMP = (
    '<hierarchy rotation="1">'
    '<node index="0" text="" resource-id="" class="android.widget.FrameLayout" '
    'content-desc="" clickable="false" bounds="[0,0][1280,800]">'
    '<node index="1" text="" resource-id="" class="android.widget.ImageButton" '
    'content-desc="Hang up call" clickable="true" bounds="[600,670][680,750]"/>'
    '</node>'
    '</hierarchy>'
)


def test_c11_find_tap_target_locates_hangup_button(zeev):
    assert zeev._c11_find_tap_target(_FAKE_UI_DUMP) == (640, 710)


def test_c11_find_tap_target_ignores_non_clickable_matches(zeev):
    xml = _FAKE_UI_DUMP.replace('clickable="true"', 'clickable="false"')
    assert zeev._c11_find_tap_target(xml) is None


def test_c11_find_tap_target_returns_none_when_absent(zeev):
    assert zeev._c11_find_tap_target("<hierarchy></hierarchy>") is None


def test_c11_tap_hangup_button_taps_the_located_target(zeev, monkeypatch):
    calls = []

    class _FakeResult:
        returncode = 0
        stdout = _FAKE_UI_DUMP
        stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert zeev._c11_tap_hangup_button("1.2.3.4:5555") is True
    assert ["adb", "-s", "1.2.3.4:5555", "shell", "input", "tap", "640", "710"] in calls


def test_c11_tap_hangup_button_returns_false_when_no_button_found(zeev, monkeypatch):
    class _FakeResult:
        returncode = 0
        stdout = "<hierarchy></hierarchy>"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: _FakeResult())
    assert zeev._c11_tap_hangup_button("1.2.3.4:5555") is False


def test_c11_call_io_overrides_hangup_only(zeev):
    """Confirms C11CallIO inherits everything else from SCOCallIO
    unchanged -- only hangup() should differ."""
    assert issubclass(zeev.C11CallIO, zeev.SCOCallIO)
    assert zeev.C11CallIO.hangup is not zeev.SCOCallIO.hangup
    for name in ("speak", "capture_popen", "fast_detect", "is_hungup",
                 "send_dtmf", "play_wav"):
        assert getattr(zeev.C11CallIO, name) is getattr(zeev.SCOCallIO, name)


def test_c11_call_io_hangup_calls_c11_gv_hangup(zeev, monkeypatch):
    called = []
    monkeypatch.setattr(zeev, "c11_gv_hangup", lambda: called.append(True) or True)
    io = zeev.C11CallIO.__new__(zeev.C11CallIO)  # skip __init__ (needs real BT)
    io.hangup()
    assert called == [True]
