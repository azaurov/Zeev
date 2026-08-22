#!/usr/bin/env python3
"""Small always-on HTTP endpoint for the Zepp OS watch app.

Deliberately a separate, lightweight process from `zeev.py --device` (which
owns the Whisplay HAT / mic / camera): it imports zeev.py as a module (same
safe pattern `.claude/skills/run-zeev/driver.py` already uses -- everything
in zeev.py is gated behind `if __name__ == "__main__":`) and only ever talks
to Bluetooth hardware via the already-shared zeev-audio daemon socket, and to
Wyze cameras over the network -- no local camera/mic device is touched, so it
can run alongside device mode without contention.

Usage:
    python3 zeev/watch_server.py [--port 5050]

Auth: every request must carry `X-Zeev-Watch-Key` matching ZEEV_WATCH_KEY
from .env. No key configured means the server refuses everything (fail
closed, not open) -- this is meant to sit behind nginx on a public hostname.
"""
import argparse
import hmac
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import zeev  # noqa: E402  (import after sys.path patch)

# Fixed pairing target: the TOZO NC9 earbuds paired on ragnarok. Not
# scan-and-guess -- a watch tap should always reconnect the same known
# headphones, never whichever device happens to answer a fresh scan first.
_BLE_TARGET_MAC = "94:4B:F8:6B:08:08"
_BLE_TARGET_NAME = "TOZO NC9"

_FIND_SMOKEY_TRANSCRIPT = "find Smokey"

# Blessings menu: cmd -> exact torah.db ref-lookup query. Fetched via
# zeev.torah_search() directly rather than the conversational needs_torah()/
# LLM path -- a blessing is short and fixed, so a deterministic DB read is
# both faster and strictly more grounded than routing through the 70B for
# text that never needs paraphrasing. Query string is the canonical DB
# spelling ("Netilat", not the Ashkenazi "Netilas" a person might say), since
# this is a hardcoded button tap, not free text -- _torah_ref_lookup's LIKE
# probe needs the literal spelling that's actually in `ref`.
_BLESSINGS = {
    "netilas_yadayim": "Netilat Yadayim",
}


def _already_connected(mac):
    return any(m == mac and connected for m, _name, connected in zeev.bt_list())


def _cmd_pair_ble():
    # BlueZ returns a spurious "br-connection-page-timeout" error when asked
    # to connect a device that's already connected (found live 2026-08-18) --
    # skip the redundant connect attempt rather than report a false failure.
    if _already_connected(_BLE_TARGET_MAC):
        return True, f"Already connected to {_BLE_TARGET_NAME}."
    if not zeev.bt_pair(_BLE_TARGET_MAC):
        return False, f"Couldn't pair {_BLE_TARGET_NAME}."
    if not zeev.bt_connect(_BLE_TARGET_MAC):
        return False, f"Paired {_BLE_TARGET_NAME} but couldn't connect."
    return True, f"Connected to {_BLE_TARGET_NAME}."


def _speak(text):
    """Fire-and-forget TTS through whatever audio device is active (BT
    headphones if connected, wired speaker otherwise) -- same daemon call
    device mode uses. Fire-and-forget, not speak_sync, because speak_sync
    blocks up to 180s: the watch app is waiting on this HTTP response with a
    spinner, and a multi-minute news digest must not hold that open. Best
    effort -- a TTS failure must not turn a working text reply into a 500.

    skip_espeak=True: this endpoint has no better fallback of its own queued
    (unlike device mode's English path, which escalates to Groq Orpheus on a
    Kokoro/Piper failure), so a failure here should mean silence, not the
    daemon quietly re-speaking the whole digest in the robotic espeak-ng
    voice -- the watch already shows the text either way.
    """
    if not (zeev._audio and zeev._audio.available):
        return
    try:
        zeev._audio.speak(text, skip_espeak=True)
    except Exception as e:
        print(f"[watch] speak failed: {e}", flush=True)


def _cmd_world_news():
    text = zeev.get_shpeel()
    _speak(text)
    return True, text


def _cmd_find_smokey():
    subj = zeev.resolve_subject(_FIND_SMOKEY_TRANSCRIPT)
    if not subj:
        return False, "Smokey isn't configured as a subject (check ZEEV_SUBJECTS)."
    reply, _frames = zeev.sweep_for_subject(subj)
    _speak(reply)
    return True, reply


def _make_blessing_cmd(query):
    def _cmd():
        rows = zeev.torah_search(query, k=1)
        if not rows:
            return False, f"Couldn't find {query} in the Torah database."
        _ref, en, _he = rows[0]
        if not en:
            return False, f"{query} has no English text in the Torah database."
        _speak(en)
        return True, en
    return _cmd


_COMMANDS = {
    "pair_ble": _cmd_pair_ble,
    "world_news": _cmd_world_news,
    "find_smokey": _cmd_find_smokey,
}
_COMMANDS.update(
    (f"blessing_{key}", _make_blessing_cmd(query)) for key, query in _BLESSINGS.items()
)


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            if not zeev.ZEEV_WATCH_KEY:
                return False
            got = self.headers.get("X-Zeev-Watch-Key", "")
            return hmac.compare_digest(got, zeev.ZEEV_WATCH_KEY)

        def do_POST(self):
            if self.path != "/watch":
                self._json(404, {"ok": False, "error": "not found"})
                return
            if not self._authorized():
                self._json(403, {"ok": False, "error": "unauthorized"})
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._json(400, {"ok": False, "error": "malformed JSON body"})
                return
            cmd = data.get("cmd")
            fn = _COMMANDS.get(cmd)
            if not fn:
                self._json(400, {"ok": False, "error": f"unknown cmd {cmd!r}"})
                return
            try:
                ok, message = fn()
            except Exception as e:
                print(f"[watch] {cmd} failed: {e}", flush=True)
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": ok, "message": message})

    return Handler


def run_watch_server(host="0.0.0.0", port=5050):
    if not zeev.ZEEV_WATCH_KEY:
        print("[watch] WARNING: ZEEV_WATCH_KEY is not set — every request will "
              "be refused. Add ZEEV_WATCH_KEY=... to .env.", flush=True)
    # Without this, zeev.bt_pair/bt_connect/bt_scan silently fall through to
    # a raw bluetoothctl subprocess (15s hard timeout on connect) instead of
    # the already-running zeev-audio daemon's fast BT path -- found live
    # 2026-08-18, a real "pair_ble" request timed out and reported failure
    # even though the earbuds connected moments later on their own.
    zeev._init_audio()
    server = ThreadingHTTPServer((host, port), _make_handler())
    print(f"[watch] listening on {host}:{port}", flush=True)
    server.serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5050)
    args = ap.parse_args()
    run_watch_server(port=args.port)


if __name__ == "__main__":
    main()
