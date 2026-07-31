"""
AudioClient — thin Python adapter over the zeev-audio Go daemon.

Connects to the Unix socket at SOCKET_PATH. All methods fall back to
returning a sentinel value (never raise) when the daemon is unavailable,
so zeev.py can degrade gracefully.

Protocol: NDJSON over Unix domain socket.
  Python → Go:  {"id": "<uuid>", "cmd": "<name>", ...fields...}\n
  Go → Python:  {"id": "<uuid>", "ok": true|false, ...fields...}\n
"""

import base64
import json
import socket
import threading
import uuid
import logging

log = logging.getLogger(__name__)

SOCKET_PATH = "/tmp/zeev-audio.sock"
DEFAULT_TIMEOUT = 20.0   # seconds — most commands (vol, bt_verify, health, ...) reply in <1s


class _Disconnected(Exception):
    pass


class AudioClient:
    """
    Thread-safe client for the zeev-audio Go daemon.

    If the daemon socket is missing at construction time, all methods
    return sensible defaults and log a warning — zeev.py falls back to
    its built-in Python implementations.
    """

    SOCKET = SOCKET_PATH

    def __init__(self, socket_path: str = SOCKET_PATH):
        self.SOCKET = socket_path
        self._sock: socket.socket | None = None
        self._reader = None
        self._lock = threading.Lock()
        self._available = False
        self._connect()

    # ── connection management ────────────────────────────────────────────────

    def _connect(self) -> bool:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(DEFAULT_TIMEOUT)
            s.connect(self.SOCKET)
            self._sock = s
            self._reader = s.makefile("r")
            self._available = True
            log.debug("audio_client: connected to %s", self.SOCKET)
            return True
        except (FileNotFoundError, ConnectionRefusedError) as e:
            log.warning("audio_client: daemon not available (%s) — using Python fallbacks", e)
            self._available = False
            return False

    def _reconnect(self) -> bool:
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._reader = None
        self._available = False
        return self._connect()

    @property
    def available(self) -> bool:
        return self._available

    # ── low-level call ───────────────────────────────────────────────────────

    def _call(self, _timeout: float = DEFAULT_TIMEOUT, _retry: bool = True, **kwargs) -> dict:
        """
        Send one request and return the parsed response.
        Reconnects once on broken pipe or read timeout; raises _Disconnected
        if still broken. `_timeout` bounds the whole request (send + reply
        readline) so a desynced or wedged daemon connection fails fast
        instead of hanging the caller forever. Leading underscore keeps it
        out of the way of JSON payload fields like `timeout` (bt_scan uses
        that name for its own scan-duration field).
        """
        if not self._available:
            raise _Disconnected("daemon not available")

        kwargs["id"] = str(uuid.uuid4())
        payload = (json.dumps(kwargs) + "\n").encode()

        attempts = 2 if _retry else 1
        for attempt in range(attempts):
            try:
                with self._lock:
                    self._sock.settimeout(_timeout)
                    self._sock.sendall(payload)
                    line = self._reader.readline()
                if not line:
                    raise _Disconnected("daemon closed connection")
                return json.loads(line)
            except (socket.timeout, BrokenPipeError, OSError, _Disconnected):
                if attempt < attempts - 1:
                    if not self._reconnect():
                        raise _Disconnected("daemon unavailable after reconnect")
                else:
                    self._available = False
                    raise _Disconnected("daemon unavailable")

    def _call_safe(self, default, _timeout: float = DEFAULT_TIMEOUT,
                   _retry: bool = True, **kwargs) -> dict:
        """Like _call but returns default on any error."""
        try:
            return self._call(_timeout=_timeout, _retry=_retry, **kwargs)
        except Exception as e:
            log.debug("audio_client: %s -> %s", kwargs.get("cmd"), e)
            return default

    # ── public API ───────────────────────────────────────────────────────────

    def audio_dev(self) -> str:
        """Return the active ALSA PCM string (BT or wired speaker)."""
        r = self._call_safe({"dev": "plughw:wm8960soundcard,0"}, cmd="audio_dev")
        return r.get("dev", "plughw:wm8960soundcard,0")

    def speak(self, text: str, lang: str = "en", dev: str = "", voice: str = "") -> None:
        """Fire-and-forget TTS — returns immediately (daemon synthesises async)."""
        self._call_safe({}, cmd="speak", text=text, lang=lang, dev=dev, voice=voice)

    def speak_sync(self, text: str, lang: str = "en", dev: str = "", voice: str = "") -> bool:
        """Blocking TTS — waits for audio to finish before returning. Returns
        True on success so callers can fall back to a different TTS path on
        failure (e.g. Russian: remote Piper on bosgame -> local Piper -> gTTS)."""
        # Long passages (e.g. Torah/parsha readings) can take well over a
        # minute to synthesize + play; give this far more headroom than the
        # default so it isn't mistaken for a wedged connection mid-speech.
        r = self._call_safe({}, _timeout=180.0, cmd="speak_sync", text=text, lang=lang, dev=dev, voice=voice)
        return bool(r.get("ok"))

    def get_volume(self) -> int:
        """Return current system volume 0–100."""
        r = self._call_safe({"level": 87}, cmd="vol_get")
        return int(r.get("level", 87))

    def set_volume(self, level: int) -> int:
        """Set system volume 0–100; returns the clamped level."""
        r = self._call_safe({"level": level}, cmd="vol_set", level=int(level))
        return int(r.get("level", level))

    def bt_detect(self) -> dict:
        """Query bluealsa-aplay at daemon startup; returns BT status dict."""
        r = self._call_safe({}, cmd="bt_detect")
        return {
            "connected": bool(r.get("connected")),
            "dev": r.get("bt_dev", ""),
            "rate": int(r.get("bt_rate", 0)),
            "channels": int(r.get("bt_channels", 0)),
        }

    def bt_verify(self) -> dict:
        """Re-check BT connection on each TTS call to detect disconnects."""
        r = self._call_safe({}, cmd="bt_verify")
        return {
            "connected": bool(r.get("connected")),
            "dev": r.get("bt_dev", ""),
            "rate": int(r.get("bt_rate", 0)),
            "channels": int(r.get("bt_channels", 0)),
        }

    def bt_scan(self, timeout: int = 10) -> list[dict]:
        """Scan for BT devices; returns list of {"mac": .., "name": ..}."""
        r = self._call_safe({"devices": []}, _timeout=timeout + 10,
                            cmd="bt_scan", timeout=timeout)
        return r.get("devices", [])

    def bt_connect(self, mac: str) -> dict:
        """Connect to a BT device by MAC; returns bt_verify-style dict."""
        r = self._call_safe({"ok": False, "error": "daemon unavailable"}, _timeout=30.0,
                            cmd="bt_connect", mac=mac)
        return r

    def bt_disconnect(self, mac: str) -> bool:
        """Disconnect a BT device; returns True on success."""
        r = self._call_safe({"ok": False}, cmd="bt_disconnect", mac=mac)
        return bool(r.get("ok"))

    def bt_pair(self, mac: str) -> bool:
        """Pair and trust a BT device; returns True on success."""
        r = self._call_safe({"ok": False}, _timeout=30.0, cmd="bt_pair", mac=mac)
        return bool(r.get("ok"))

    def play(self, query: str):
        """Start YouTube playback. Returns (title, error); error is None on success.

        Long timeout and no retry, both measured rather than guessed: resolving
        a track with yt-dlp on a Pi Zero 2W takes ~45s, so the old 30s timeout
        always expired -- and the retry re-sent the same request, starting a
        second competing download instead of recovering. Worse, the failure
        surfaced as an empty title, which the caller turned into "Playing X"
        while nothing played.
        """
        r = self._call_safe(None, _timeout=150.0, _retry=False,
                            cmd="play", query=query)
        if not r:
            return None, "audio daemon did not respond"
        if not r.get("ok"):
            return None, r.get("error") or "playback failed"
        return r.get("title") or "", None

    def speak_stop(self) -> None:
        """Cancel in-progress speech playback (daemon-side).

        Deliberately opens its own short-lived connection instead of going
        through _call: that path serializes every request behind self._lock on
        a single socket, so a stop sent while speak_sync was in flight would
        block until the speech it was meant to cancel had already finished.
        The daemon handles each connection in its own goroutine, so a second
        connection is served immediately.
        """
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(self.SOCKET)
            s.sendall((json.dumps({"cmd": "speak_stop", "id": str(uuid.uuid4())}) + "\n").encode())
            try:
                s.recv(256)
            except socket.timeout:
                pass
            s.close()
        except Exception as e:
            log.debug("audio_client: speak_stop -> %s", e)

    def stop(self) -> None:
        """Stop any in-progress music playback."""
        self._call_safe({}, cmd="stop")

    def record(self, max_seconds: float = 8.0, vad: bool = True, rate: int = 0,
               silence_rms: float = 0.0) -> bytes:
        """Record audio; returns WAV bytes. rate=0 → 16000 Hz.

        max_seconds is a ceiling, not a duration: with VAD the daemon returns as
        soon as the speaker stops. silence_rms is this room's measured noise
        floor (0 → daemon default); the daemon cannot measure it itself because
        the mic only opens once the user is already talking.
        """
        r = self._call_safe({"wav_b64": ""}, _timeout=max_seconds + 15,
                            cmd="record", max_seconds=max_seconds, vad=vad, rate=rate,
                            silence_rms=silence_rms)
        b64 = r.get("wav_b64", "")
        if not b64:
            return b""
        return base64.b64decode(b64)

    def sco_speak(self, text: str, sco_dev: str, sco_rate: int) -> bool:
        """Synthesize text via Piper and play on the SCO ALSA device.
        sco_rate is the negotiated HFP sample rate (8000 or 16000 Hz).
        Returns True on success."""
        r = self._call_safe({"ok": False}, _timeout=60.0, cmd="speak_sco",
                            text=text, dev=sco_dev, rate=sco_rate)
        return bool(r.get("ok"))

    def sco_record(self, sco_dev: str, sco_rate: int,
                   max_seconds: float = 8.0, vad: bool = True) -> bytes:
        """Record from an SCO capture device at the negotiated rate.
        Returns WAV bytes (header uses sco_rate)."""
        r = self._call_safe({"wav_b64": ""}, _timeout=max_seconds + 15,
                            cmd="sco_record", dev=sco_dev, rate=sco_rate,
                            max_seconds=max_seconds, vad=vad)
        b64 = r.get("wav_b64", "")
        if not b64:
            return b""
        return base64.b64decode(b64)

    def health(self) -> str:
        """Return a one-line health summary string."""
        r = self._call_safe({"title": "unavailable"}, cmd="health")
        return r.get("title", "unavailable")

    def close(self) -> None:
        """Close the socket connection."""
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._available = False
