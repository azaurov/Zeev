"""
GVSession / GVCallIO — Google Voice calling backend for Zeev, driving
voice.google.com in a real Chrome browser instead of a Bluetooth HFP phone.

Why this exists: bt_call_loop's SCO backend needs a real phone in Bluetooth
range. This backend places genuine Google Voice VoIP calls instead — no
phone/SIM/carrier involved at all. Proven live 2026-09-11 (see project
memory `gv_vm_calling_investigation.md`): three real calls placed from
voice.google.com in a Chrome instance on bosgame, confirmed end-to-end
including the human on the far end hearing real Zeev TTS "loud and clear."

Audio routing: two persistent virtual PipeWire sinks (defined in
~/.config/pipewire/pipewire-pulse.conf.d/99-dogcaller.conf, NOT created by
this module — see ensure_virtual_sinks()'s docstring for why) —
`dogcaller_call_out` (Chrome's selected speaker; its .monitor is where the
call's incoming audio can be captured) and `dogcaller_mic_feed` (Chrome's
selected microphone; playing audio into this sink is what the far end
hears as Zeev's voice). Chrome is launched with PULSE_SINK/PULSE_SOURCE
env vars pinning it to these two devices specifically — this is load-
bearing: bosgame's real default sink is a Bluetooth yard speaker, and a
Chrome instance launched without this pin would play call audio there.

Why not qemu/Android: a same-night investigation into routing this through
an Android emulator (Google Voice's actual app, not the web client) hit a
proven-dead-end broken audio HAL — see the same memory doc. This browser
approach is unrelated to that dead end and does not share its problems.
"""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

try:
    import websocket  # pip install websocket-client
except ImportError:
    websocket = None

CHROME_PROFILE_DIR = os.path.expanduser("~/.config/zeev-gv-chrome")
CDP_PORT = 9334  # distinct from any ad-hoc debug port used during manual testing
PULSE_SINK = "dogcaller_call_out"     # Chrome's speaker -- call audio arrives here
PULSE_SOURCE = "dogcaller_mic_feed.monitor"  # Chrome's mic -- what Chrome "hears"
MIC_FEED_SINK = "dogcaller_mic_feed"  # play into this sink to have Zeev "speak"

VOICE_URL = "https://voice.google.com/u/0/calls"
SINK_WAIT_TIMEOUT = 15  # seconds


class GVCallError(RuntimeError):
    pass


def ensure_virtual_sinks(timeout: float = SINK_WAIT_TIMEOUT) -> None:
    """Refuse to proceed until dogcaller_call_out/dogcaller_mic_feed are
    actually enumerable by PulseAudio, same reasoning as vm_launch.sh's own
    sink-wait: if PULSE_SINK names a sink that doesn't exist yet, libpulse
    silently falls back to the *default* sink -- which on bosgame is a
    Bluetooth yard speaker, not somewhere call audio should ever go. Better
    to fail loudly here than let that happen silently.

    Does NOT create the sinks itself -- they're defined declaratively in
    ~/.config/pipewire/pipewire-pulse.conf.d/99-dogcaller.conf (persists
    across a pipewire-pulse restart, unlike a one-off `pactl load-module`)
    so this stays a pure readiness check, not a place a second competing
    definition could drift from that file.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            out = subprocess.run(
                ["pactl", "list", "short", "sinks"],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except Exception:
            out = ""
        if f"\t{PULSE_SINK}\t" in out and f"\t{MIC_FEED_SINK}\t" in out:
            return
        time.sleep(1)
    raise GVCallError(
        f"virtual sinks {PULSE_SINK!r}/{MIC_FEED_SINK!r} never appeared after "
        f"{timeout}s -- refusing to launch Chrome (would fall back to the "
        "default/yard-speaker sink). Check "
        "~/.config/pipewire/pipewire-pulse.conf.d/99-dogcaller.conf and that "
        "pipewire-pulse is running."
    )


def _display_env() -> dict:
    """DISPLAY/XAUTHORITY for bosgame's real GNOME/Xwayland session --
    same resolution vm_launch.sh already does (XAUTHORITY's path has a
    random suffix regenerated every login/reboot)."""
    env = dict(os.environ)
    env["DISPLAY"] = ":0"
    try:
        candidates = sorted(
            (p for p in os.listdir("/run/user/1000") if p.startswith(".mutter-Xwaylandauth.")),
            key=lambda p: os.path.getmtime(os.path.join("/run/user/1000", p)),
            reverse=True,
        )
    except FileNotFoundError:
        candidates = []
    if not candidates:
        raise GVCallError(
            "no .mutter-Xwaylandauth.* file under /run/user/1000 -- is the "
            "GNOME session up on bosgame?"
        )
    env["XAUTHORITY"] = os.path.join("/run/user/1000", candidates[0])
    return env


class _CDP:
    """Minimal Chrome DevTools Protocol client -- tab discovery + JS eval,
    formalizing the hand-driven spike from the same-night investigation.
    Deliberately not a general CDP library: this project needs exactly
    Runtime.evaluate (drive the page) and Page.captureScreenshot
    (diagnostics), nothing else."""

    def __init__(self, port: int = CDP_PORT):
        self.port = port

    def _tab_ws_url(self, title_contains: str = "Voice") -> str:
        try:
            tabs = json.loads(
                urllib.request.urlopen(f"http://localhost:{self.port}/json", timeout=5).read()
            )
        except (urllib.error.URLError, OSError) as e:
            raise GVCallError(f"could not reach Chrome DevTools on port {self.port}: {e}") from e
        for t in tabs:
            if t.get("type") == "page" and title_contains.lower() in (t.get("title") or "").lower():
                return t["webSocketDebuggerUrl"]
        for t in tabs:
            if t.get("type") == "page":
                return t["webSocketDebuggerUrl"]
        raise GVCallError("no page tab found in Chrome DevTools tab list")

    def eval_js(self, expr: str, timeout: float = 10):
        if websocket is None:
            raise GVCallError("websocket-client not installed (pip install websocket-client)")
        ws = websocket.create_connection(self._tab_ws_url(), timeout=timeout)
        try:
            ws.send(json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": expr, "returnByValue": True, "awaitPromise": True},
            }))
            while True:
                resp = json.loads(ws.recv())
                if resp.get("id") == 1:
                    break
        finally:
            ws.close()
        result = resp.get("result", {}).get("result", {})
        if "value" in result:
            return result["value"]
        # exceptionDetails or a non-JSON-serializable result (undefined, etc.)
        exc = resp.get("result", {}).get("exceptionDetails")
        if exc:
            raise GVCallError(f"page JS threw: {exc}")
        return None


class GVSession:
    """Owns the Chrome/CDP lifecycle for a single Google Voice call. One
    Chrome instance is reused across calls (login persists in the
    profile dir) -- launch_or_reuse() is idempotent."""

    def __init__(self, port: int = CDP_PORT):
        self.cdp = _CDP(port)
        self.port = port

    def launch_or_reuse(self) -> None:
        try:
            urllib.request.urlopen(f"http://localhost:{self.port}/json", timeout=3)
            return  # already running
        except (urllib.error.URLError, OSError):
            pass
        ensure_virtual_sinks()
        os.makedirs(CHROME_PROFILE_DIR, exist_ok=True)
        env = _display_env()
        env["PULSE_SINK"] = PULSE_SINK
        env["PULSE_SOURCE"] = PULSE_SOURCE
        subprocess.Popen(
            [
                "setsid", "google-chrome",
                f"--user-data-dir={CHROME_PROFILE_DIR}",
                f"--remote-debugging-port={self.port}",
                f"--remote-allow-origins=http://localhost:{self.port}",
                "--use-fake-ui-for-media-stream",  # auto-accepts mic permission; still uses real devices
                "--new-window", VOICE_URL,
            ],
            env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://localhost:{self.port}/json", timeout=2)
                time.sleep(1)  # let the Voice SPA finish its own initial load
                return
            except (urllib.error.URLError, OSError):
                time.sleep(1)
        raise GVCallError("Chrome did not come up with DevTools reachable within 20s")

    def dial(self, number: str) -> None:
        """Set the dial-pad input and click Call. React's controlled input
        does not update from a plain `.value = ...` -- found live: must go
        through the native HTMLInputElement value setter, then dispatch a
        real 'input' event so React's onChange actually fires. React then
        re-renders the Call button (its aria-label includes the dialed
        digits) on the *next* tick, not synchronously within this same
        script -- found live: querying for the button immediately after
        dispatching 'input' reported "call button not found" even though
        the field itself showed the number correctly and the button was
        visibly present a moment later. A short await before the second
        query is enough margin for that re-render to land."""
        digits = re.sub(r"[^\d+]", "", number)
        result = self.cdp.eval_js(f"""
        (async () => {{
          const input = document.querySelector('input[placeholder*="name or number" i]');
          if (!input) return {{ok:false, reason:'dial input not found', dump: document.body.innerText.slice(0,500)}};
          const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          nativeSetter.call(input, {digits!r});
          input.dispatchEvent(new Event('input', {{bubbles: true}}));
          await new Promise(r => setTimeout(r, 500));
          const btn = document.querySelector('button[aria-label*="call" i]');
          if (!btn) return {{ok:false, reason:'call button not found', value: input.value, dump: document.body.innerText.slice(0,500)}};
          btn.click();
          return {{ok:true, value: input.value, ariaLabel: btn.getAttribute('aria-label')}};
        }})()
        """)
        if not result or not result.get("ok"):
            raise GVCallError(f"dial({number!r}) failed: {result}")

    def state(self) -> str:
        """'active' | 'ended' | 'idle'.

        Found live 2026-09-11, took three attempts to pin down correctly
        -- record here so a future selector change doesn't have to
        re-derive this. Two signals tried and REJECTED, both false
        positives that fire during ringing, before the far end actually
        answers: (1) the "Hang up call" button -- present from the moment
        dialing starts, since it can cancel a still-ringing call too; (2)
        "Ongoing call" / "Call with <name>" text -- also present while
        merely ringing, not only once connected. Both made
        run_call_mode's "wait for the call to connect before starting
        bt_call_loop's clock" fix a no-op (state() reported 'active'
        ~1-1.5s after dial, i.e. during ringing), which is why three live
        test calls in a row got mistaken for voicemail despite the user
        answering and speaking immediately -- bt_call_loop's 25s
        no-answer timeout kept effectively counting from dial time, not
        from actual pickup.

        The REAL signal, confirmed by diffing 30 one-second DOM snapshots
        across a real ring → answer → hangup cycle: the page shows
        "Calling…" while ringing, which is replaced by "Elapsed time N
        seconds" (alongside a running 00:MM timer) at the exact moment the
        far end picks up -- verified as the only text that changes at that
        boundary, isolated from an unrelated, coincidentally-timed search-
        autocomplete UI change nearby that looked like a candidate at
        first but wasn't (disappeared ~14s after the real answer, not at
        it).

        Dumps page text on a miss so a future Google DOM change fails
        loud rather than silently reporting the wrong state."""
        result = self.cdp.eval_js("""
        (() => {
          const full = document.body.innerText;
          if (full.includes('Elapsed time')) return {state:'active'};
          if (full.includes('How was the quality of your call')) return {state:'ended'};
          return {state:'idle', dump: full.slice(0,300)};
        })()
        """)
        return (result or {}).get("state", "idle")

    def hangup(self) -> None:
        self.cdp.eval_js("""
        (() => {
          const btn = document.querySelector('button[aria-label="Hang up call"]');
          if (btn) btn.click();
          return {clicked: !!btn};
        })()
        """)


def _paplay(sink: str, wav_bytes: bytes = None, path: str = None) -> None:
    cmd = ["paplay", f"--device={sink}"]
    if path:
        subprocess.run(cmd + [path], check=False)
    else:
        subprocess.run(cmd, input=wav_bytes, check=False)


class GVCallIO:
    """Backend object passed as bt_call_loop's `call_io` param. Implements
    the same method set SCOCallIO does (see zeev.py) so bt_call_loop's
    internals don't need to know or care which backend is in play.

    speak_sco_fn/vad_collect_fn are zeev.py's own bt_speak_sco/_vad_collect,
    passed in by the caller (run_call_mode) rather than imported here.
    Deliberate: zeev.py is normally run as `__main__` (`python3
    zeev/zeev.py --call ...`), which never registers itself under the name
    "zeev" in sys.modules -- a plain `import zeev` from inside this module
    would silently trigger a SECOND, independent execution of the entire
    ~17k-line file (reprinting its startup banner, re-initializing a second
    _audio client, etc.) rather than reusing the one already running.
    Passing the functions in avoids that trap entirely.

    fast_detect() is a deliberate MVP simplification, not full SCO parity
    -- see the plan doc / CLAUDE.md follow-up notes: it does a plain
    capture instead of bt_fast_detect's onset-heuristic optimization,
    returning ("unknown", "") so bt_call_loop falls through to its
    existing LLM-based call-type classification path.
    """

    samplerate = 16000  # what capture_popen()/speak() normalize to; parec/ffmpeg do the resampling

    def __init__(self, session: GVSession, speak_sco_fn, vad_collect_fn):
        self.session = session
        self._speak_sco = speak_sco_fn
        self._vad_collect = vad_collect_fn

    def speak(self, text: str, persona: str = "assistant", lang: str = "en",
              record_path: str | None = None) -> None:
        # bt_speak_sco pipes raw S16LE mono PCM at `samplerate` straight to
        # this command's stdin (no WAV header) -- paplay needs the matching
        # --raw/--format/--rate/--channels flags or it defaults to 44.1kHz
        # stereo and plays back at the wrong speed/pitch.
        self._speak_sco(
            text, sco_dev="(unused-gv)", samplerate=self.samplerate,
            persona=persona, record_path=record_path, lang=lang,
            play_cmd=["paplay", f"--device={MIC_FEED_SINK}",
                      "--raw", "--format=s16le", f"--rate={self.samplerate}", "--channels=1"],
        )

    def capture_popen(self) -> subprocess.Popen:
        return subprocess.Popen(
            ["parec", "--device=dogcaller_call_out.monitor",
             "--raw", "--format=s16le", f"--rate={self.samplerate}", "--channels=1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )

    def fast_detect(self):
        """MVP: plain capture, no onset heuristic. Returns
        (pcm_bytes, "unknown", "") -- bt_call_loop treats an empty
        transcript from fast_detect as "run STT/classification normally,"
        which is already how it handles a genuine bt_fast_detect miss."""
        rec = self.capture_popen()
        pcm = self._vad_collect(rec, samplerate=self.samplerate, silence_ms=900)
        rec.terminate()
        rec.wait()
        return pcm, "unknown", ""

    def is_hungup(self) -> bool:
        return self.session.state() != "active"

    def hangup(self) -> None:
        self.session.hangup()

    def send_dtmf(self, digit: str) -> bool:
        print(f"[gv-call] DTMF ({digit!r}) requested but not supported through "
              "Google Voice's web UI yet -- skipping", flush=True)
        return False

    def play_wav(self, path: str) -> bool:
        _paplay(MIC_FEED_SINK, path=path)
        return True
