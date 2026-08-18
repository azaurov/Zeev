"""Zepp OS watch endpoint (zeev/watch_server.py).

Exercises the HTTP handler end-to-end (real socket, real threads) rather than
poking at internals, since the whole point of this module is the wire
contract a phone-side fetch() will actually hit: auth header, JSON shape,
status codes.
"""
import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "zeev"))


@pytest.fixture
def watch_server(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "ZEEV_WATCH_KEY", "test-secret")
    import watch_server as ws

    server = ThreadingHTTPServer(("127.0.0.1", 0), ws._make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield ws, server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _post(port, path, body=None, key="test-secret"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-Zeev-Watch-Key"] = key
    payload = json.dumps(body).encode() if body is not None else b""
    conn.request("POST", path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def test_missing_key_rejected(watch_server):
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "world_news"}, key=None)
    assert status == 403
    assert data["ok"] is False


def test_wrong_key_rejected(watch_server):
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "world_news"}, key="nope")
    assert status == 403


def test_no_configured_key_refuses_everything(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "ZEEV_WATCH_KEY", "")
    import watch_server as ws
    server = ThreadingHTTPServer(("127.0.0.1", 0), ws._make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _data = _post(server.server_address[1], "/watch",
                               {"cmd": "world_news"}, key="anything")
    finally:
        server.shutdown()
        thread.join(timeout=5)
    assert status == 403


def test_unknown_cmd_rejected(watch_server):
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "bogus"})
    assert status == 400
    assert data["ok"] is False


def test_malformed_json_rejected(watch_server):
    _ws, port = watch_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/watch", body=b"{not json",
                 headers={"X-Zeev-Watch-Key": "test-secret"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    assert resp.status == 400
    assert data["ok"] is False


def test_unknown_path_404(watch_server):
    _ws, port = watch_server
    status, _data = _post(port, "/nope", {"cmd": "world_news"})
    assert status == 404


def test_world_news_dispatches_get_shpeel(watch_server, zeev, monkeypatch):
    _ws, port = watch_server
    monkeypatch.setattr(zeev, "get_shpeel", lambda: "Today's shpeel: quiet everywhere.")
    status, data = _post(port, "/watch", {"cmd": "world_news"})
    assert status == 200
    assert data == {"ok": True, "message": "Today's shpeel: quiet everywhere."}


def test_pair_ble_success(watch_server, zeev, monkeypatch):
    ws, port = watch_server
    monkeypatch.setattr(zeev, "bt_list", lambda: [])
    monkeypatch.setattr(zeev, "bt_pair", lambda mac: mac == ws._BLE_TARGET_MAC)
    monkeypatch.setattr(zeev, "bt_connect", lambda mac: mac == ws._BLE_TARGET_MAC)
    status, data = _post(port, "/watch", {"cmd": "pair_ble"})
    assert status == 200
    assert data["ok"] is True
    assert ws._BLE_TARGET_NAME in data["message"]


def test_pair_ble_already_connected_skips_reconnect(watch_server, zeev, monkeypatch):
    # BlueZ returns a spurious page-timeout error when asked to connect an
    # already-connected device -- bt_pair/bt_connect must not even be called.
    ws, port = watch_server
    monkeypatch.setattr(zeev, "bt_list", lambda: [(ws._BLE_TARGET_MAC, "TOZO NC9", True)])
    monkeypatch.setattr(zeev, "bt_pair", lambda mac: (_ for _ in ()).throw(AssertionError("should not be called")))
    monkeypatch.setattr(zeev, "bt_connect", lambda mac: (_ for _ in ()).throw(AssertionError("should not be called")))
    status, data = _post(port, "/watch", {"cmd": "pair_ble"})
    assert status == 200
    assert data["ok"] is True
    assert "Already connected" in data["message"]


def test_pair_ble_pair_failure(watch_server, zeev, monkeypatch):
    monkeypatch.setattr(zeev, "bt_list", lambda: [])
    monkeypatch.setattr(zeev, "bt_pair", lambda mac: False)
    monkeypatch.setattr(zeev, "bt_connect", lambda mac: True)
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "pair_ble"})
    assert status == 200
    assert data["ok"] is False


def test_pair_ble_connect_failure(watch_server, zeev, monkeypatch):
    monkeypatch.setattr(zeev, "bt_list", lambda: [])
    monkeypatch.setattr(zeev, "bt_pair", lambda mac: True)
    monkeypatch.setattr(zeev, "bt_connect", lambda mac: False)
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "pair_ble"})
    assert status == 200
    assert data["ok"] is False


def test_find_smokey_dispatches_sweep(watch_server, zeev, monkeypatch):
    subj = {"name": "Smokey", "kind": "cat", "cams": ["basement-cam"]}
    monkeypatch.setattr(zeev, "resolve_subject", lambda text: subj)
    monkeypatch.setattr(zeev, "sweep_for_subject",
                         lambda s, **kw: ("Smokey is on the basement cam.", 1))
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "find_smokey"})
    assert status == 200
    assert data == {"ok": True, "message": "Smokey is on the basement cam."}


def test_find_smokey_unconfigured(watch_server, zeev, monkeypatch):
    monkeypatch.setattr(zeev, "resolve_subject", lambda text: None)
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "find_smokey"})
    assert status == 200
    assert data["ok"] is False


def test_command_exception_returns_500(watch_server, zeev, monkeypatch):
    def _boom():
        raise RuntimeError("network is down")
    monkeypatch.setattr(zeev, "get_shpeel", _boom)
    _ws, port = watch_server
    status, data = _post(port, "/watch", {"cmd": "world_news"})
    assert status == 500
    assert data["ok"] is False
