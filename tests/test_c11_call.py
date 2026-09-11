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
    assert zeev.c11_gv_dial("not a number") is False


def test_c11_gv_dial_formats_ten_digit_number_with_country_code(zeev, monkeypatch):
    """Found live: GV's ACTION_CALL intent needs the full +1 country code --
    a plain 10-digit tel: URI gets rejected."""
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
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

    class _FakeResult:
        returncode = 1
        stdout = ""
        stderr = "error: device offline"

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: _FakeResult())
    assert zeev.c11_gv_dial("5081234567") is False


def test_c11_gv_dial_returns_false_on_exception(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")

    def _raise(cmd, **kwargs):
        raise FileNotFoundError("adb not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert zeev.c11_gv_dial("5081234567") is False


def test_c11_bt_mac_has_a_sensible_default(zeev):
    assert zeev.C11_BT_MAC.count(":") == 5  # a MAC address shape


# ---------------------------------------------------------------------------
# c11_gv_hangup() / C11CallIO -- AT+CHUP is accepted by C11's Bluetooth stack
# but does NOT actually end a Google Voice SelfManaged call (confirmed live:
# sent AT+CHUP, got "OK", the call was still visibly active moments later).
# C11CallIO overrides hangup() to use KEYCODE_ENDCALL instead.
# ---------------------------------------------------------------------------

def test_c11_gv_hangup_requires_adb_serial(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "")
    assert zeev.c11_gv_hangup() is False


def test_c11_gv_hangup_sends_endcall_keyevent(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")
    captured = {}

    class _FakeResult:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert zeev.c11_gv_hangup() is True
    assert captured["cmd"] == ["adb", "-s", "1.2.3.4:5555", "shell",
                                "input", "keyevent", "KEYCODE_ENDCALL"]


def test_c11_gv_hangup_returns_false_on_exception(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "c11_adb_serial", lambda: "1.2.3.4:5555")

    def _raise(cmd, **kwargs):
        raise FileNotFoundError("adb not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert zeev.c11_gv_hangup() is False


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
