"""AudioClient reconnect behavior (zeev/audio_client.py).

Focused on one bug: once a long-running caller's connection to the daemon
drops (daemon restart, crash), _call() used to latch `_available = False`
and never try again -- every subsequent call silently failed via
_call_safe's default-value fallback, with no visible symptom. Found live
2026-08-22 via watch_server.py going silently mute after a zeev-audio
restart until the watch process itself was also restarted.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "zeev"))

import audio_client as ac


class _FakeSocket:
    def __init__(self, reply=b'{"ok": true}\n'):
        self.reply = reply
        self.sent = []
        self.closed = False

    def settimeout(self, t):
        pass

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


class _FakeReader:
    def __init__(self, line=b'{"ok": true}\n'):
        self.line = line

    def readline(self):
        return self.line


def _client_without_real_connect():
    """An AudioClient whose __init__ never touches a real socket."""
    client = ac.AudioClient.__new__(ac.AudioClient)
    client.SOCKET = ac.SOCKET_PATH
    client._sock = None
    client._reader = None
    client._lock = __import__("threading").Lock()
    client._available = False
    return client


def test_call_reconnects_when_available_is_stale_false(monkeypatch):
    client = _client_without_real_connect()

    def fake_connect():
        client._sock = _FakeSocket()
        client._reader = _FakeReader()
        client._available = True
        return True

    monkeypatch.setattr(client, "_connect", fake_connect)
    result = client._call(cmd="health")
    assert result == {"ok": True}
    assert client._available is True


def test_call_raises_when_reconnect_also_fails(monkeypatch):
    client = _client_without_real_connect()
    monkeypatch.setattr(client, "_connect", lambda: False)
    try:
        client._call(cmd="health")
        assert False, "expected _Disconnected"
    except ac._Disconnected:
        pass


def test_call_safe_recovers_after_daemon_restart(monkeypatch):
    """The realistic path: _call_safe (what every AudioClient method uses)
    must actually recover, not just silently keep returning the default."""
    client = _client_without_real_connect()

    def fake_connect():
        client._sock = _FakeSocket()
        client._reader = _FakeReader(b'{"ok": true, "level": 42}\n')
        client._available = True
        return True

    monkeypatch.setattr(client, "_connect", fake_connect)
    result = client._call_safe({"level": 0}, cmd="vol_get")
    assert result == {"ok": True, "level": 42}
