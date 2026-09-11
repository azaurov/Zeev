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
