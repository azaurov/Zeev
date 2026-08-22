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
import re
import requests
import shutil
import subprocess
import sys
import threading
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

# import_sefaria.py strips the <i class="footnote">...</i> body but leaves the
# bare superscript reference digit stuck to the preceding word ("Blessed1
# are You... Adonoy2 our God") -- invisible when the LLM paraphrases this text
# on its way to speech, but this endpoint speaks torah_search() output
# directly with no LLM in between, so "one"/"two" would otherwise get spoken
# aloud. Matches only a letter immediately followed by 1-2 digits then a word
# boundary (not space-separated numbers like verse/chapter counts).
_FOOTNOTE_MARKER_RE = re.compile(r"(?<=[a-zA-Z])\d{1,2}\b")


def _strip_footnote_markers(text):
    return _FOOTNOTE_MARKER_RE.sub("", text)


# The Tetragrammaton (YHVH) is never read aloud as written -- tradition
# substitutes "Adonai" in prayer. Google's TTS has no notion of this (it's
# not a word in its Hebrew training data) and either sounds out the raw
# letters or garbles them, which is exactly the "didn't pronounce the
# Adonai" Alex heard live (2026-08-22). Matched with optional niqud
# (vowel-point combining marks, U+0591-U+05C7) between each consonant since
# the DB text is fully vowelized and a plain "יהוה" literal would never
# match it.
_NIQUD_CHARS = r"[֑-ׇ]"
_TETRAGRAMMATON_RE = re.compile(
    r"י" + _NIQUD_CHARS + r"*ה" + _NIQUD_CHARS + r"*ו" + _NIQUD_CHARS + r"*ה" + _NIQUD_CHARS + r"*"
)
_ADONAI = "אֲדֹנָי"


def _substitute_tetragrammaton(text):
    return _TETRAGRAMMATON_RE.sub(_ADONAI, text)


# Google's Hebrew TTS is trained almost entirely on plain, unvocalized modern
# Hebrew -- the heavily-pointed liturgical text in torah.db is a different
# register it handles poorly, garbling ordinary words like "Eloheinu" (found
# live 2026-08-22, right after fixing the Tetragrammaton). Stripping niqud
# reduces the text to the standard printed form of the same words (verified:
# this exact blessing's niqud-stripped text matches how a siddur prints it
# unvocalized) -- a root-cause fix rather than patching one mispronounced
# word at a time.
_NIQUD_RE = re.compile(_NIQUD_CHARS + "+")


def _strip_niqud(text):
    return _NIQUD_RE.sub("", text)


# Blessing audio is Hebrew at half speed (Alex explicitly wants to hear the
# pronunciation, not a fast English paraphrase) -- the Go daemon has no
# Hebrew path at all (Piper/Kokoro are en/ru/es only, see CLAUDE.md
# Multilingual TTS), so this goes through gTTS + ffmpeg + mpg123 directly in
# this process, the same tools zeev.py's own speak_terminal() uses for
# Hebrew. atempo (not mpg123's --pitch) because atempo is a pure time-stretch
# -- it keeps the voice's pitch natural instead of dropping it into a slowed-
# record register, at the cost of a bit of extra ffmpeg CPU on the Pi Zero.
_BLESSING_TEMPO = 0.65


def _current_audio_dev():
    """Whatever ALSA PCM the Go daemon currently has active (BT headphones if
    connected, wired speaker otherwise) -- watch_server.py has no local BT
    state of its own (zeev.bt_audio_dev()'s _BT_AUDIO_DEV global is only ever
    populated by device mode's own BT init, which never runs in this
    process), so the daemon's own live query is the only source of truth
    here."""
    if zeev._audio and zeev._audio.available:
        try:
            return zeev._audio.audio_dev() or "default"
        except Exception:
            pass
    return "default"


def _speak_hebrew_slow(text, tempo=_BLESSING_TEMPO):
    """Fire-and-forget: gTTS chunks -> ffmpeg atempo (pitch-preserving
    slowdown) -> mpg123. Runs in a background thread, same fire-and-forget
    reasoning as _speak() -- the HTTP response must not wait on this.
    Best-effort: any failure here must not turn a working text reply into a
    500, so everything below is wrapped and only logged.
    """
    if not (shutil.which("mpg123") and shutil.which("ffmpeg")):
        print("[watch] mpg123/ffmpeg not available, cannot speak Hebrew", flush=True)
        return

    def _run():
        adev = _current_audio_dev()
        try:
            for chunk in zeev._gtts_chunks(text):
                mp3 = zeev._gtts_fetch_chunk(chunk, "he")
                if not mp3:
                    continue
                ffmpeg = subprocess.Popen(
                    ["ffmpeg", "-loglevel", "error", "-i", "pipe:0",
                     "-af", f"atempo={tempo}", "-f", "mp3", "pipe:1"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
                slowed, _ = ffmpeg.communicate(input=mp3, timeout=30)
                if not slowed:
                    continue
                player = subprocess.Popen(
                    ["mpg123", "-q", "-a", adev, "-"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                player.communicate(input=slowed, timeout=60)
        except Exception as e:
            print(f"[watch] Hebrew speak failed: {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()


# Cartesia's sonic-3.5 has real native Hebrew prosody (sonic-2, used
# elsewhere in this project for English phone-call TTS via zeev.cartesia_tts,
# is English-only -- Hebrew only arrived with sonic-3). Requested live
# 2026-08-22 after gTTS still botched vowel stress even post niqud-fix --
# gTTS is a Google Translate hack, not a real phonetic model, and was never
# going to get stress right. Full niqud text is passed through UNSTRIPPED
# here (opposite of the gTTS path): a real phonetic model should use the
# vowel points to get stress right, not choke on them the way gTTS did.
_CARTESIA_HEBREW_MODEL = "sonic-3.5"


def _cartesia_tts_hebrew(text):
    """Returns WAV bytes or None (no key configured, HTTP error, network
    failure) -- every failure mode falls back to the gTTS path, never to
    silence."""
    if not zeev.CARTESIA_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={"X-API-Key": zeev.CARTESIA_API_KEY,
                     "Cartesia-Version": "2024-06-10",
                     "Content-Type": "application/json"},
            json={"model_id": _CARTESIA_HEBREW_MODEL,
                  "transcript": text[:4000],
                  "voice": {"mode": "id", "id": zeev.CARTESIA_VOICE_ID},
                  "language": "he",
                  "output_format": {"container": "wav", "encoding": "pcm_s16le",
                                     "sample_rate": 22050}},
            timeout=20,
        )
        if resp.status_code != 200:
            print(f"[watch] Cartesia Hebrew TTS HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
            return None
        return resp.content
    except Exception as e:
        print(f"[watch] Cartesia Hebrew TTS error: {e}", flush=True)
        return None


def _speak_hebrew(text_niqud, text_no_niqud, tempo=_BLESSING_TEMPO):
    """Fire-and-forget: try Cartesia (native Hebrew prosody) first, fall back
    to the gTTS pipeline (_speak_hebrew_slow) on any failure -- no API key,
    an HTTP error, a network blip, or ffmpeg/mpg123 producing nothing. Runs
    in its own thread so a slow/failed Cartesia call can't hold up the HTTP
    response; the gTTS fallback spawns its own thread in turn, which is
    harmless (both are still fire-and-forget, just one thread deep).
    """
    def _run():
        wav = _cartesia_tts_hebrew(text_niqud)
        if not wav:
            _speak_hebrew_slow(text_no_niqud, tempo=tempo)
            return
        if not (shutil.which("mpg123") and shutil.which("ffmpeg")):
            print("[watch] mpg123/ffmpeg not available, cannot play Cartesia Hebrew audio", flush=True)
            _speak_hebrew_slow(text_no_niqud, tempo=tempo)
            return
        adev = _current_audio_dev()
        try:
            # Re-encoded to mp3 (not played as the wav Cartesia returns)
            # because mpg123, not aplay, is what's proven safe against
            # BlueALSA's strict format matching in this project (see
            # CLAUDE.md's BT audio resampling note) -- same reasoning the
            # gTTS path already relies on.
            ffmpeg = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error", "-i", "pipe:0",
                 "-af", f"atempo={tempo}", "-f", "mp3", "pipe:1"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            slowed, _ = ffmpeg.communicate(input=wav, timeout=30)
            if not slowed:
                _speak_hebrew_slow(text_no_niqud, tempo=tempo)
                return
            player = subprocess.Popen(
                ["mpg123", "-q", "-a", adev, "-"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            player.communicate(input=slowed, timeout=60)
        except Exception as e:
            print(f"[watch] Cartesia Hebrew playback failed: {e}", flush=True)
            _speak_hebrew_slow(text_no_niqud, tempo=tempo)

    threading.Thread(target=_run, daemon=True).start()


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
        _ref, en, he = rows[0]
        if not en:
            return False, f"{query} has no English text in the Torah database."
        text = _strip_footnote_markers(en)
        # Spoken in Hebrew, slowed -- Alex wants to hear the actual
        # pronunciation, not a fast English reading. Watch screen still shows
        # the English text: Zepp OS's bitmap fonts aren't guaranteed to
        # render Hebrew glyphs, and the English is what's actually readable.
        if he:
            tetra_fixed = _substitute_tetragrammaton(_strip_footnote_markers(he))
            _speak_hebrew(tetra_fixed, _strip_niqud(tetra_fixed))
        else:
            _speak(text)
        return True, text
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
