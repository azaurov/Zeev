#!/usr/bin/env python3
"""Zeev — AI Companion"""

import base64
import io
import json
import os
import re
import readline
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
HISTORY_FILE  = BASE_DIR / "data" / "history.jsonl"
MEMORY_FILE   = BASE_DIR / "data" / "user_memory.json"
NOTES_FILE    = BASE_DIR / "data" / "notes.jsonl"
RL_HISTORY    = BASE_DIR / "data" / ".readline_history"
SETTINGS_FILE = BASE_DIR / "data" / "settings.json"
TORAH_DB      = BASE_DIR / "data" / "torah.db"

# Load .env from repo root if present (supplements environment variables)
_ENV_FILE = BASE_DIR.parent / ".env"
if _ENV_FILE.exists():
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TTS_URL = "https://api.groq.com/openai/v1/audio/speech"
GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_URL     = "https://api.tavily.com/search"

# ---------------------------------------------------------------------------
# Multi-provider config  (set via .env)
# ---------------------------------------------------------------------------

# LLM_SERVER: groq | openai | anthropic | gemini | ollama | openrouter
LLM_SERVER  = os.environ.get("LLM_SERVER",  "groq")
# STT_SERVER: groq | openai | vosk | faster-whisper
STT_SERVER  = os.environ.get("STT_SERVER",  "groq")
# TTS_SERVER: auto | orpheus | openai | piper | gtts | espeak
#   auto = try orpheus (en) → gtts (he/es/ru) → piper → espeak
TTS_SERVER  = os.environ.get("TTS_SERVER",  "auto")

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY",  "")
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY",     "")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY",     "")
OLLAMA_URL         = os.environ.get("OLLAMA_URL",         "http://localhost:11434")
OLLAMA_MODEL       = os.environ.get("OLLAMA_MODEL",       "llama3.2")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENAI_TTS_VOICE   = os.environ.get("OPENAI_TTS_VOICE",   "alloy")
OPENAI_STT_MODEL   = os.environ.get("OPENAI_STT_MODEL",   "whisper-1")

# Wake-word (openwakeword)
WAKE_WORD_ENABLED   = os.environ.get("WAKE_WORD_ENABLED",   "").lower() == "true"
WAKE_WORD_MODELS    = os.environ.get("WAKE_WORD_MODELS",    "")   # comma-sep names/paths
WAKE_WORD_THRESHOLD = float(os.environ.get("WAKE_WORD_THRESHOLD", "0.5"))
WAKE_WORD_COOLDOWN  = float(os.environ.get("WAKE_WORD_COOLDOWN",  "1.5"))

# Learning state — populated by init_learning() at startup
USER_FACTS       = []   # persistent facts about the user
USER_NOTES       = []   # persistent notes saved by the user
_HISTORY_ENTRIES = []   # raw parsed entries from history.jsonl for RAG
_HISTORY_INDEX   = {}   # word → [entry indices] inverted index
_notes_lock      = threading.Lock()

_STOP_WORDS = frozenset([
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "was", "one", "our", "out", "get", "has", "him", "his", "how",
    "its", "may", "new", "now", "old", "see", "two", "who", "did",
    "let", "say", "she", "too", "use", "that", "this", "with", "have",
    "from", "they", "will", "been", "what", "when", "your", "than",
    "just", "more", "also", "into", "then", "some", "could", "would",
    "about", "there", "their", "which", "were", "does", "very", "like",
])

_SEARCH_RE = re.compile(
    r"\b(weather|forecast|news|today|tonight|tomorrow|latest|current|score|"
    r"stock|price|who won|happening|right now|recently|this week|this month|"
    r"just announced|breaking|update|2024|2025|2026)\b",
    re.IGNORECASE,
)

def needs_search(text):
    return bool(_SEARCH_RE.search(text))

_MUSIC_RE = re.compile(
    r"^(start playing|can you play|put on|play me|i want to hear|i want to listen to|"
    r"throw on|blast|queue|play)\s+(?P<query>.+)",
    re.IGNORECASE,
)

def extract_music_query(text):
    """Return the song/artist query if text is a play request, else None."""
    m = _MUSIC_RE.match(text.strip())
    return m.group("query").strip() if m else None

# Model auto-routing heuristics
_REASONING_RE = re.compile(
    r"\b(prove|proof|calculate|solve|equation|formula|math|"
    r"step.by.step|walk me through|think through|deduce|infer|"
    r"probability|logic|algorithm|complexity|optimize|"
    r"puzzle|riddle|paradox|theorem|derive|derivation|"
    r"geometric|algebraic|differential|integral|calculus)\b",
    re.IGNORECASE,
)
_SMART_RE = re.compile(
    r"\b(write|implement|code|function|class|debug|refactor|"
    r"script|program|python|javascript|typescript|rust|golang|java|sql|"
    r"explain|summarize|compare|analyze|analyse|essay|report|draft|"
    r"architecture|design|difference between|how does|how do|regex|"
    r"tell me about|what is|what are|why does|why is|why are|"
    r"history of|overview of|describe|definition|meaning of|"
    r"pros and cons|advantages|disadvantages|recommend|suggest|"
    r"how to|tutorial|guide|best practice|strategy|approach)\b",
    re.IGNORECASE,
)

MODELS = {
    "1": ("llama-3.1-8b-instant",          "Llama 3.1 8B  — fast"),
    "2": ("llama-3.3-70b-versatile",        "Llama 3.3 70B — smart"),
    "3": ("deepseek-r1-distill-llama-70b",  "DeepSeek R1   — reasoning"),
}
_MODEL_SHORT = {
    "llama-3.1-8b-instant":         "8B",
    "llama-3.3-70b-versatile":      "70B",
    "deepseek-r1-distill-llama-70b":"R1",
}
PRIOR_TURNS  = 15
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

CAMERA_AVAILABLE  = False   # set by init_camera()
THERMAL_AVAILABLE = False   # set by init_thermal()
CAMERA_FLIP       = False   # set by load_settings()
FORCED_LANG       = None    # None = auto; 'en'/'he'/'es'/'ru' = locked language
_MUSIC_PROC       = None    # active mpg123 playback process
_VOLUME           = 75      # 0–100; applied via amixer


def route_model(text):
    """Pick model ID automatically based on message content."""
    if _REASONING_RE.search(text):
        return MODELS["3"][0]   # DeepSeek R1
    if _SMART_RE.search(text):
        return MODELS["2"][0]   # 70B Smart
    return MODELS["1"][0]       # 8B Fast


def model_label(model_id):
    return _MODEL_SHORT.get(model_id, "?")


# ---------------------------------------------------------------------------
# PiSugar battery
# ---------------------------------------------------------------------------

def _pisugar_query(cmd):
    """Send a command to the PiSugar server socket. Returns response or None."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("127.0.0.1", 8423))
        s.sendall((cmd + "\n").encode())
        data = s.recv(64).decode().strip()
        s.close()
        return data
    except Exception:
        return None


_batt_state      = (None, None)   # (level: float|None, charging: bool|None)
_batt_state_lock = threading.Lock()


def _batt_poll_loop(interval=30):
    """Background thread: poll PiSugar every `interval` seconds."""
    global _batt_state
    while True:
        level_resp = _pisugar_query("get battery")
        if level_resp and "I2C not connected" not in level_resp:
            charge_resp = _pisugar_query("get battery_charging")
        else:
            charge_resp = None
        try:
            level = float(level_resp.split(": ")[1])
        except (AttributeError, IndexError, ValueError):
            level = None
        try:
            charging = charge_resp.split(": ")[1].strip().lower() == "true"
        except (AttributeError, IndexError):
            charging = None
        with _batt_state_lock:
            _batt_state = (level, charging)
        time.sleep(interval)


def get_battery():
    """Return (level: float|None, charging: bool|None) from the background poller."""
    with _batt_state_lock:
        return _batt_state


# ---------------------------------------------------------------------------
# Bluetooth device management + terminal TTS
# ---------------------------------------------------------------------------

TTS_AVAILABLE    = False   # set by init_tts()
PIPER_BIN        = ""
PIPER_MODELS     = {}      # lang -> model path, populated by init_tts()
_piper_term_proc = None    # persistent Piper process for terminal mode
_piper_term_lock = threading.Lock()

# Hebrew Unicode block: U+0590–U+05FF
_HE_RE = re.compile(r"[֐-׿]")
# Cyrillic block: U+0400–U+04FF
_RU_RE = re.compile(r"[Ѐ-ӿ]")
# Spanish markers: ñ, ¿, ¡, or common accented vowels
_ES_RE = re.compile(r"[ñÑ¿¡áéíóúüÁÉÍÓÚÜ]")


def detect_lang(text):
    """Return 'he', 'ru', 'es', or 'en' based on characters in text."""
    if _HE_RE.search(text):
        return "he"
    if _RU_RE.search(text):
        return "ru"
    if _ES_RE.search(text):
        return "es"
    return "en"


def init_tts():
    global TTS_AVAILABLE, PIPER_BIN, PIPER_MODELS
    PIPER_BIN = shutil.which("piper") or ""
    piper_dir = Path.home() / "piper"
    data_dir  = BASE_DIR / "data"
    share_dir = Path.home() / ".local" / "share" / "piper"

    def _find(candidates):
        for c in candidates:
            if Path(c).exists():
                return str(c)
        return ""

    if PIPER_BIN:
        PIPER_MODELS["en"] = os.environ.get("PIPER_MODEL", "") or _find([
            data_dir / "piper_voice.onnx",
            piper_dir / "en_US-ryan-medium.onnx",
            share_dir / "en_US-ryan-medium.onnx",
            piper_dir / "en_US-lessac-medium.onnx",
            share_dir / "en_US-lessac-medium.onnx",
        ])
        PIPER_MODELS["es"] = _find([
            piper_dir / "es_MX-ald-medium.onnx",
            share_dir / "es_MX-ald-medium.onnx",
        ])
        PIPER_MODELS["ru"] = _find([
            piper_dir / "ru_RU-dmitri-medium.onnx",
            share_dir / "ru_RU-dmitri-medium.onnx",
        ])

    TTS_AVAILABLE = bool(
        (PIPER_BIN and PIPER_MODELS.get("en")) or shutil.which("espeak-ng")
    )


_YHWH_RE = re.compile(r"י[ְ-ׇ]*ה[ְ-ׇ]*ו[ְ-ׇ]*ה[ְ-ׇ]*|ה[ְ-ׇ]*ו[ְ-ׇ]*י[ְ-ׇ]*|הוהיְ")

def _clean_for_tts(text, lang=None):
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"[*_`#~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Replace the Tetragrammaton (with or without vowel points) reverently
    replacement = "אֲדֹנָי" if lang == "he" else "Adonai"
    text = _YHWH_RE.sub(replacement, text)
    return text


def _piper_speak(clean, model):
    """Speak via a persistent Piper process → aplay, in a background thread.
    The process stays alive between calls so the model is only loaded once."""
    global _piper_term_proc

    def _run():
        global _piper_term_proc
        with _piper_term_lock:
            try:
                if _piper_term_proc is None or _piper_term_proc.poll() is not None:
                    _piper_term_proc = subprocess.Popen(
                        [PIPER_BIN, "--model", model, "--output_raw"],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
                p = _piper_term_proc
                p.stdin.write(clean.encode() + b"\n")
                p.stdin.flush()
                # Stream PCM from Piper → aplay as it arrives so playback
                # starts immediately rather than waiting for full synthesis.
                aplay = subprocess.Popen(
                    ["aplay", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                fd = p.stdout.fileno()
                got_audio = False
                try:
                    while True:
                        # Wait longer on the first chunk — piper loads its ONNX model,
                        # which takes ~20s on Pi Zero 2W. Use 5s between chunks: piper
                        # synthesises sentence-by-sentence and takes ~2-4s per sentence.
                        t = 30.0 if not got_audio else 5.0
                        ready, _, _ = select.select([fd], [], [], t)
                        if not ready:
                            break
                        try:
                            chunk = os.read(fd, 4096)
                        except OSError:
                            break
                        if not chunk:
                            break
                        got_audio = True
                        aplay.stdin.write(chunk)
                    aplay.stdin.close()
                except BrokenPipeError:
                    pass
                aplay.wait()
            except Exception:
                _piper_term_proc = None

    threading.Thread(target=_run, daemon=True).start()


def _piper_warmup():
    """Start the persistent Piper process in the background at launch so the
    model is loaded before the first TTS call."""
    if not (PIPER_BIN and PIPER_MODELS.get("en")):
        return
    global _piper_term_proc

    def _start():
        global _piper_term_proc
        with _piper_term_lock:
            if _piper_term_proc is None or _piper_term_proc.poll() is not None:
                _piper_term_proc = subprocess.Popen(
                    [PIPER_BIN, "--model", PIPER_MODELS["en"], "--output_raw"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )

    threading.Thread(target=_start, daemon=True).start()


def _gtts_chunks(text, limit=200):
    """Split text into chunks ≤ limit chars, breaking at sentence boundaries."""
    parts = re.split(r'(?<=[.!?״,])\s+', text)
    chunk = ""
    for p in parts:
        if len(chunk) + len(p) + 1 <= limit:
            chunk = (chunk + " " + p).strip()
        else:
            if chunk:
                yield chunk
            chunk = p
    if chunk:
        yield chunk


def _gtts_fetch_chunk(chunk, lang):
    """Fetch one chunk from Google Translate TTS. Returns MP3 bytes or None."""
    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_tts",
            params={"ie": "UTF-8", "q": chunk, "tl": lang, "client": "gtx"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        return resp.content if resp.status_code == 200 else None
    except Exception:
        return None


def _gtts_speak(text, lang, adev=None):
    """Speak text via Google Translate TTS + mpg123 in a background thread.
    All chunks are fetched in parallel so playback of chunk N overlaps with
    the network fetch of chunk N+1, eliminating the inter-sentence gap."""
    def _run():
        import concurrent.futures
        chunks = list(_gtts_chunks(text))
        if not chunks:
            return
        try:
            cmd = ["mpg123", "-q"] + (["-a", adev] if adev else []) + ["-"]
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                futs = [ex.submit(_gtts_fetch_chunk, c, lang) for c in chunks]
                for fut in futs:
                    mp3 = fut.result()
                    if mp3:
                        proc = subprocess.Popen(
                            cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        proc.stdin.write(mp3)
                        proc.stdin.close()
                        proc.wait()
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Startup / shutdown cleanup
# ---------------------------------------------------------------------------

def zeev_cleanup():
    """Kill Zeev-owned subprocesses and temp files. Safe to call at startup and exit."""
    import glob as _glob

    # Kill tracked globals
    global _MUSIC_PROC, _piper_term_proc
    for proc in (_MUSIC_PROC, _piper_term_proc):
        try:
            if proc and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=2)
        except Exception:
            pass
    _MUSIC_PROC = None
    _piper_term_proc = None

    # Kill any orphaned processes that were piped through zeev temp files
    for pattern in ("zeev_music", "zeev_rec.wav"):
        try:
            subprocess.run(
                ["pkill", "-f", pattern],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    # Remove leftover temp audio files from any previous run
    for path in _glob.glob("/tmp/zeev_*"):
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# YouTube / music playback
# ---------------------------------------------------------------------------

YT_COOKIES = BASE_DIR / "data" / "yt-cookies.txt"


def music_stop():
    """Kill the active music playback process, if any."""
    global _MUSIC_PROC
    if _MUSIC_PROC and _MUSIC_PROC.poll() is None:
        _MUSIC_PROC.terminate()
        _MUSIC_PROC = None
        return True
    _MUSIC_PROC = None
    return False


def set_volume(level):
    """Set system volume (0–100). Tries amixer Master, then wm8960 Speaker."""
    global _VOLUME
    level = max(0, min(100, int(level)))
    _VOLUME = level
    # Standard ALSA Master control (works on most Pi audio configs)
    r = subprocess.run(
        ["amixer", "sset", "Master", f"{level}%"],
        capture_output=True,
    )
    if r.returncode != 0:
        # WM8960 HAT: Speaker range is 0–160 (120 ≈ 75%)
        raw = round(level * 1.6)
        subprocess.run(
            ["amixer", "-c", "wm8960soundcard", "sset", "Speaker", str(raw)],
            capture_output=True,
        )
    return level


def get_volume():
    return _VOLUME


def youtube_play(query, adev=None):
    """Search YouTube for query, download audio, and play via ffmpeg+aplay in background."""
    global _MUSIC_PROC

    music_stop()  # stop any current track

    if not shutil.which("yt-dlp"):
        return None, "yt-dlp not installed (sudo apt install yt-dlp)"
    if not shutil.which("ffmpeg"):
        return None, "ffmpeg not installed (sudo apt install ffmpeg)"

    def _run():
        global _MUSIC_PROC
        tmp = f"/tmp/zeev_music_{os.getpid()}.%(ext)s"
        tmp_glob = f"/tmp/zeev_music_{os.getpid()}.*"
        try:
            print(f"\n{DIM}[music] Searching: {query}...{RESET}", flush=True)

            # Build command: use cookies if available, otherwise try without
            cookies_args = ["--cookies", str(YT_COOKIES)] if YT_COOKIES.exists() else []
            dl = subprocess.run(
                ["yt-dlp", f"ytsearch1:{query}",
                 "-o", tmp, "-f", "bestaudio", "--no-playlist", "-q",
                 "--print", "before_dl:%(title)s",
                 "--extractor-args", "youtube:player_client=ios,mweb"]
                + cookies_args,
                capture_output=True, text=True,
            )

            import glob as _glob
            files = _glob.glob(tmp_glob)
            if not files:
                stderr = dl.stderr
                if "no longer valid" in stderr or "cookies" in stderr.lower():
                    print(f"\n{DIM}[music] YouTube cookies expired. Re-export from your browser "
                          f"and save to {YT_COOKIES}{RESET}", flush=True)
                elif "Sign in" in stderr or "bot" in stderr:
                    print(f"\n{DIM}[music] YouTube requires login. Save cookies to "
                          f"{YT_COOKIES} using 'Get cookies.txt LOCALLY' browser extension.{RESET}",
                          flush=True)
                else:
                    print(f"\n{DIM}[music] Could not download: {query}{RESET}", flush=True)
                return

            title = dl.stdout.strip().splitlines()[0] if dl.stdout.strip() else query
            audio_file = files[0]
            print(f"\n{DIM}[playing: {title}]{RESET}", flush=True)

            aplay_cmd = ["aplay", "-r", "44100", "-f", "S16_LE", "-c", "2", "-q", "-"]
            if adev:
                aplay_cmd = ["aplay", "-D", adev, "-r", "44100", "-f", "S16_LE", "-c", "2", "-q", "-"]

            ffmpeg_p = subprocess.Popen(
                ["ffmpeg", "-i", audio_file, "-f", "s16le", "-ar", "44100", "-ac", "2", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            aplay_p = subprocess.Popen(
                aplay_cmd, stdin=ffmpeg_p.stdout,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            ffmpeg_p.stdout.close()
            _MUSIC_PROC = aplay_p
            aplay_p.wait()
            ffmpeg_p.wait()
        except Exception as e:
            print(f"\n{DIM}[music] Error: {e}{RESET}")
        finally:
            import glob as _glob
            for f in _glob.glob(tmp_glob):
                try:
                    os.unlink(f)
                except OSError:
                    pass
            _MUSIC_PROC = None

    threading.Thread(target=_run, daemon=True).start()
    return query, None


def speak_terminal(text, lang=None):
    """Speak via Piper (en/es) or espeak-ng (he/fallback) in background."""
    if not TTS_AVAILABLE:
        return
    lang = lang or detect_lang(text)
    clean = _clean_for_tts(text, lang)
    if not clean:
        return

    _GTTS_LANGS = {"he": "he", "es": "es", "ru": "ru"}
    if lang in _GTTS_LANGS and shutil.which("mpg123"):
        _gtts_speak(clean, _GTTS_LANGS[lang])
    elif PIPER_BIN and PIPER_MODELS.get("en"):
        _piper_speak(clean, PIPER_MODELS["en"])
    else:
        espeak_lang = {"he": "he", "es": "es", "ru": "ru"}.get(lang, "en")
        try:
            subprocess.Popen(
                ["espeak-ng", "-s", "145", "-v", espeak_lang, clean],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def groq_tts(text):
    """Call Groq Orpheus TTS (English only). Returns WAV bytes or None."""
    if not GROQ_API_KEY or not text.strip():
        return None
    clean = _clean_for_tts(text, "en")
    if not clean or detect_lang(clean) != "en":
        return None
    try:
        resp = requests.post(
            GROQ_TTS_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "canopylabs/orpheus-v1-english",
                  "input": clean[:4096], "voice": "daniel", "response_format": "wav"},
            timeout=30,
        )
        return resp.content if resp.status_code == 200 else None
    except Exception:
        return None


def _openai_tts(text, lang="en"):
    """Call OpenAI TTS. Returns MP3 bytes or None."""
    if not OPENAI_API_KEY or not text.strip():
        return None
    clean = _clean_for_tts(text, lang)
    if not clean:
        return None
    try:
        resp = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "tts-1", "input": clean[:4096],
                  "voice": OPENAI_TTS_VOICE, "response_format": "mp3"},
            timeout=30,
        )
        return resp.content if resp.status_code == 200 else None
    except Exception:
        return None


def tts_web(text, lang=None):
    """Return audio bytes (WAV or MP3) for the web /tts endpoint.
    Dispatches to the active TTS provider; returns (bytes, content_type) or (None, None)."""
    lang = lang or detect_lang(text)
    clean = _clean_for_tts(text, lang)
    if not clean:
        return None, None

    server = TTS_SERVER

    if server == "openai" or (server == "auto" and OPENAI_API_KEY and lang == "en"):
        mp3 = _openai_tts(clean, lang)
        if mp3:
            return mp3, "audio/mpeg"

    if server in ("orpheus", "auto") and lang == "en":
        wav = groq_tts(clean)
        if wav:
            return wav, "audio/wav"

    if server in ("gtts", "auto") and lang in ("he", "es", "ru"):
        chunks = list(_gtts_chunks(clean))
        parts = []
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(_gtts_fetch_chunk, c, lang) for c in chunks]
            for fut in futs:
                mp3 = fut.result()
                if mp3:
                    parts.append(mp3)
        if parts:
            return b"".join(parts), "audio/mpeg"

    if server in ("piper", "auto") and PIPER_BIN and PIPER_MODELS.get(lang, PIPER_MODELS.get("en")):
        model = PIPER_MODELS.get(lang) or PIPER_MODELS.get("en")
        try:
            p = subprocess.run(
                [PIPER_BIN, "--model", model, "--output_raw"],
                input=clean.encode(), capture_output=True, timeout=30,
            )
            if p.returncode == 0 and p.stdout:
                import wave, io
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(22050)
                    wf.writeframes(p.stdout)
                return buf.getvalue(), "audio/wav"
        except Exception:
            pass

    return None, None


def _whisper_multipart(url, api_key, wav_bytes, model):
    """POST WAV to an OpenAI-compatible /audio/transcriptions endpoint."""
    boundary = b"--boundary\r\n"
    cd = b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'
    model_part = (
        b"--boundary\r\n"
        b'Content-Disposition: form-data; name="model"\r\n\r\n'
        + model.encode() + b"\r\n"
    )
    multipart = boundary + cd + wav_bytes + b"\r\n" + model_part + b"--boundary--\r\n"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "multipart/form-data; boundary=boundary"},
            data=multipart, timeout=30,
        )
        if r.status_code == 200:
            return r.json().get("text", "").strip()
    except Exception:
        pass
    return ""


def groq_stt(wav_bytes):
    """Send WAV bytes to Groq Whisper. Returns transcript string or ''."""
    if not GROQ_API_KEY or not wav_bytes:
        return ""
    return _whisper_multipart(GROQ_STT_URL, GROQ_API_KEY, wav_bytes, "whisper-large-v3-turbo")


def _stt_vosk(wav_bytes):
    """Transcribe with local Vosk. Returns transcript or ''."""
    try:
        import vosk, wave, io, json as _json
        wf = wave.open(io.BytesIO(wav_bytes))
        rec = vosk.KaldiRecognizer(
            vosk.Model(os.environ.get("VOSK_MODEL_PATH", "/usr/share/vosk/model")),
            wf.getframerate(),
        )
        results = []
        while True:
            data = wf.readframes(4000)
            if not data:
                break
            if rec.AcceptWaveform(data):
                results.append(_json.loads(rec.Result()).get("text", ""))
        results.append(_json.loads(rec.FinalResult()).get("text", ""))
        return " ".join(r for r in results if r).strip()
    except Exception:
        return ""


def _stt_faster_whisper(wav_bytes):
    """Transcribe with local faster-whisper. Returns transcript or ''."""
    try:
        import tempfile, os as _os
        from faster_whisper import WhisperModel
        model_size = os.environ.get("FASTER_WHISPER_MODEL", "tiny.en")
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            tmp = f.name
        segments, _ = model.transcribe(tmp, beam_size=5)
        text = " ".join(s.text for s in segments).strip()
        _os.unlink(tmp)
        return text
    except Exception:
        return ""


def stt(wav_bytes):
    """Dispatch to the active STT provider. Returns transcript or ''."""
    if not wav_bytes:
        return ""
    provider = STT_SERVER
    if provider == "groq":
        return groq_stt(wav_bytes)
    if provider == "openai":
        if not OPENAI_API_KEY:
            return groq_stt(wav_bytes)   # fall back to groq
        return _whisper_multipart(
            "https://api.openai.com/v1/audio/transcriptions",
            OPENAI_API_KEY, wav_bytes, OPENAI_STT_MODEL,
        )
    if provider == "vosk":
        return _stt_vosk(wav_bytes)
    if provider == "faster-whisper":
        return _stt_faster_whisper(wav_bytes)
    return groq_stt(wav_bytes)   # fallback


def bt_list():
    """Return list of (mac, name, connected) for all paired BT devices."""
    try:
        out = subprocess.check_output(
            ["bluetoothctl", "devices"], stderr=subprocess.DEVNULL, timeout=5
        ).decode()
        devices = []
        for line in out.splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) < 3:
                continue
            mac, name = parts[1], parts[2]
            try:
                info = subprocess.check_output(
                    ["bluetoothctl", "info", mac],
                    stderr=subprocess.DEVNULL, timeout=5,
                ).decode()
                connected = "Connected: yes" in info
            except Exception:
                connected = False
            devices.append((mac, name, connected))
        return devices
    except Exception:
        return []


def bt_connect(mac):
    try:
        r = subprocess.run(
            ["bluetoothctl", "connect", mac],
            capture_output=True, timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def bt_disconnect(mac):
    try:
        subprocess.run(
            ["bluetoothctl", "disconnect", mac],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


SYSTEM_PROMPT = (
    "You are Zeev, a humble, calm, innovative and charismatic companion. "
    "You speak concisely, remember what the user tells you, and ask follow-up "
    "questions to understand them better. "
    "You are talking to Ragnar."
)
_vocab_path = BASE_DIR.parent / "swiftkey_system_prompt_snippet.md"
if _vocab_path.exists():
    SYSTEM_PROMPT += "\n\n" + _vocab_path.read_text().strip()

CYAN  = "\033[96m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_prior():
    if not HISTORY_FILE.exists():
        return []
    lines = HISTORY_FILE.read_text().strip().splitlines()
    messages = []
    for line in lines[-(PRIOR_TURNS * 2):]:
        try:
            entry = json.loads(line)
            messages.append({"role": entry["role"], "content": entry["content"]})
        except (json.JSONDecodeError, KeyError):
            pass
    return messages


def append_message(role, content):
    HISTORY_FILE.parent.mkdir(exist_ok=True)
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps({
            "role": role,
            "content": content,
            "ts": datetime.now().isoformat(),
        }) + "\n")


# ---------------------------------------------------------------------------
# User memory (persistent facts)
# ---------------------------------------------------------------------------

def load_memory():
    if not MEMORY_FILE.exists():
        return []
    try:
        data = json.loads(MEMORY_FILE.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def save_memory(facts):
    MEMORY_FILE.parent.mkdir(exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(facts, indent=2))


def extract_memory(session_msgs):
    """Extract new user facts from session_msgs via the active LLM. Updates USER_FACTS in-place."""
    global USER_FACTS
    if not session_msgs:
        return USER_FACTS
    transcript = "\n".join(
        ("USER" if m["role"] == "user" else "ZEEV") + ": " + m["content"]
        for m in session_msgs[-30:]
    )
    existing_str = "\n".join(f"- {f}" for f in USER_FACTS) if USER_FACTS else "(none)"
    user_prompt = (
        f"Conversation transcript:\n{transcript}\n\n"
        f"Already known facts about the user:\n{existing_str}\n\n"
        "List NEW facts revealed about the USER in this transcript. "
        "Focus on: location, job, preferences, hobbies, relationships, goals, habits.\n"
        'Output a JSON object: {"facts": ["...", "..."]}. If nothing new: {"facts": []}'
    )
    msgs = [
        {"role": "system", "content": "You are a data extractor. Output only valid JSON."},
        {"role": "user", "content": user_prompt},
    ]
    text, err = _llm_complete(msgs, MODELS["1"][0], max_tokens=300, json_mode=True)
    if err == "rate-limited":
        return None
    if err or not text:
        return USER_FACTS
    try:
        new_facts = json.loads(text).get("facts", [])
        if isinstance(new_facts, list):
            merged = list(USER_FACTS)
            for f in new_facts:
                if isinstance(f, str) and f.strip() and f.strip() not in merged:
                    merged.append(f.strip())
            USER_FACTS = merged
            save_memory(USER_FACTS)
    except (json.JSONDecodeError, ValueError):
        pass
    return USER_FACTS


# ---------------------------------------------------------------------------
# Notes (persistent user-saved notes)
# ---------------------------------------------------------------------------

def load_notes():
    if not NOTES_FILE.exists():
        return []
    notes = []
    for line in NOTES_FILE.read_text().splitlines():
        try:
            notes.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return notes


def add_note(text):
    global USER_NOTES
    note = {"text": text.strip(), "ts": datetime.now().isoformat()}
    NOTES_FILE.parent.mkdir(exist_ok=True)
    with open(NOTES_FILE, "a") as f:
        f.write(json.dumps(note) + "\n")
    with _notes_lock:
        USER_NOTES.append(note)
    return USER_NOTES


def delete_note(idx):
    global USER_NOTES
    with _notes_lock:
        if not (0 <= idx < len(USER_NOTES)):
            return False
        USER_NOTES.pop(idx)
        NOTES_FILE.write_text(
            "".join(json.dumps(n) + "\n" for n in USER_NOTES)
        )
    return True


# ---------------------------------------------------------------------------
# Persistent settings
# ---------------------------------------------------------------------------

_SETTINGS_TTS_ON = True   # default; overwritten by load_settings()

def load_settings():
    global CAMERA_FLIP, _SETTINGS_TTS_ON
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        CAMERA_FLIP      = bool(data.get("camera_flip", False))
        _SETTINGS_TTS_ON = bool(data.get("tts_on", True))
    except (FileNotFoundError, json.JSONDecodeError):
        pass

def save_settings():
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({
        "camera_flip": CAMERA_FLIP,
        "tts_on":      _SETTINGS_TTS_ON,
    }))

# ---------------------------------------------------------------------------
# History RAG (keyword retrieval over past conversations)
# ---------------------------------------------------------------------------

def _tokenize(text):
    return {w for w in re.findall(r"\b[a-z]{3,}\b", text.lower()) if w not in _STOP_WORDS}


def build_rag_index():
    """Parse history.jsonl into module-level globals for retrieval."""
    global _HISTORY_ENTRIES, _HISTORY_INDEX
    if not HISTORY_FILE.exists():
        return
    entries = []
    index = {}
    for line in HISTORY_FILE.read_text().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    for i, entry in enumerate(entries):
        for word in _tokenize(entry.get("content", "")):
            index.setdefault(word, []).append(i)
    _HISTORY_ENTRIES = entries
    _HISTORY_INDEX   = index


def retrieve_relevant(query, k=2, min_score=2):
    """Return up to k (user_msg, assistant_reply) pairs most relevant to query."""
    if not _HISTORY_INDEX or not _HISTORY_ENTRIES:
        return []
    scores = {}
    for word in _tokenize(query):
        for idx in _HISTORY_INDEX.get(word, []):
            scores[idx] = scores.get(idx, 0) + 1
    if not scores:
        return []
    ranked = sorted(scores, key=lambda i: -scores[i])
    pairs = []
    seen = set()
    for idx in ranked:
        if scores[idx] < min_score or len(pairs) >= k:
            break
        if idx in seen:
            continue
        entry = _HISTORY_ENTRIES[idx]
        role  = entry.get("role")
        if role == "user":
            seen.add(idx)
            for j in range(idx + 1, min(idx + 4, len(_HISTORY_ENTRIES))):
                if _HISTORY_ENTRIES[j].get("role") == "assistant" and j not in seen:
                    pairs.append((entry["content"], _HISTORY_ENTRIES[j]["content"]))
                    seen.add(j)
                    break
        elif role == "assistant":
            seen.add(idx)
            for j in range(idx - 1, max(idx - 4, -1), -1):
                if _HISTORY_ENTRIES[j].get("role") == "user" and j not in seen:
                    pairs.append((_HISTORY_ENTRIES[j]["content"], entry["content"]))
                    seen.add(j)
                    break
    return pairs


# ---------------------------------------------------------------------------
# Torah RAG (Tanakh / Mishna / Gemara via local SQLite FTS5)
# ---------------------------------------------------------------------------

_TORAH_RE = re.compile(
    r"\b("
    r"torah|tanakh|talmud|gemara|gemorah|mishna|mishnah|chumash|"
    r"bible|biblical|verse|pasuk|passuk|parasha|parashat|"
    r"halacha|halakha|midrash|rashi|rambam|maimonides|"
    r"daf|folio|tractate|masechet|seder|sefer|"
    r"genesis|bereshit|beresheet|exodus|shemot|leviticus|vayikra|"
    r"numbers|bamidbar|deuteronomy|devarim|"
    r"psalms|tehillim|proverbs|mishlei|isaiah|yeshayahu|"
    r"jeremiah|yirmiyahu|ezekiel|yechezkel|"
    r"ruth|esther|job|iyov|ecclesiastes|kohelet|"
    r"song.of.songs|shir.hashirim|lamentations|eichah|"
    r"berakhot|shabbat|eruvin|pesachim|yoma|sukkah|"
    r"rosh.hashanah|taanit|megillah|hagigah|"
    r"yevamot|ketubot|nedarim|nazir|sotah|gittin|kiddushin|"
    r"bava.kamma|bava.metzia|bava.batra|sanhedrin|makkot|"
    r"shevuot|avodah.zarah|avot|pirkei|"
    r"dead.sea.scrolls|qumran|dss|essene|essenes|community.rule|"
    r"damascus.document|war.scroll|thanksgiving.hymns|hodayot|"
    r"temple.scroll|pesher|genesis.apocryphon|4qmmt|"
    r"sumerian|akkadian|mesopotamian|babylonian.myth|gilgamesh|enkidu|"
    r"enki|enlil|inanna|ishtar|marduk|ninhursag|etcsl|"
    r"zohar|kabbalah|kabbalistic|sefirot|sephirot|sephiroth|ein.sof|"
    r"idra|tikkunim|tikkun|sitra.achra|shekhinah|shekhina|"
    r"apocrypha|deuterocanonical|"
    r"ben.sira|sirach|ecclesiasticus|tobit|judith|maccabees|maccabean|"
    r"wisdom.of.solomon|baruch|manasseh|"
    r"siddur|haggadah|haggada|machzor|piyyut|liturgy|"
    r"shacharit|mincha|maariv|musaf|neilah|"
    r"amidah|shmoneh.esrei|shemoneh.esreh|aleinu|kaddish|kedushah|"
    r"modeh.ani|ashrei|hallel|adon.olam|yigdal|birkat.hamazon|bentching|"
    r"seder|maggid|dayenu|afikomen|maror|matzah|matza|"
    r"shema|tefillin|mezuzah|mitzvah|mitzvot|tzitzit|tzedakah|teshuvah|"
    r"kashrut|kosher|shabbos|yom.tov|chag|omer|sefirat"
    r")\b",
    re.I,
)


def needs_torah(text):
    return TORAH_DB.exists() and bool(_TORAH_RE.search(text))


def torah_search(query, k=3):
    """Return up to k (ref, en_text) pairs from the local Torah FTS5 database."""
    if not TORAH_DB.exists():
        return []
    try:
        import sqlite3 as _sqlite3
        con = _sqlite3.connect(f"file:{TORAH_DB}?mode=ro", uri=True)
        words = re.findall(r"\b\w{3,}\b", query.lower())
        # FTS5 query: OR over content words, skip common stop words
        skip = {"the", "and", "for", "what", "does", "how", "who", "was",
                "are", "this", "that", "with", "from", "have"}
        fts_words = [w for w in words if w not in skip][:12]
        if not fts_words:
            return []
        fts_q = " OR ".join(fts_words)
        rows = con.execute(
            "SELECT ref, en FROM passages WHERE passages MATCH ? ORDER BY rank LIMIT ?",
            (fts_q, k),
        ).fetchall()
        con.close()
        return rows
    except Exception:
        return []


def init_learning():
    """Load memory facts, notes, settings, and build RAG index. Call once at startup."""
    global USER_FACTS, USER_NOTES
    USER_FACTS = load_memory()
    USER_NOTES = load_notes()
    load_settings()
    build_rag_index()
    threading.Thread(target=_batt_poll_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Tavily search
# ---------------------------------------------------------------------------

def tavily_search(query):
    if not TAVILY_API_KEY:
        return "Search unavailable: TAVILY_API_KEY not set."
    try:
        resp = requests.post(
            TAVILY_URL,
            json={"api_key": TAVILY_API_KEY, "query": query, "max_results": 5},
            timeout=10,
        )
        results = resp.json().get("results", [])
        if not results:
            return "No results found."
        return "\n\n".join(
            f"{r['title']}\n{r['url']}\n{r.get('content', '')}" for r in results
        )
    except Exception as e:
        return f"Search error: {e}"


# ---------------------------------------------------------------------------
# Groq streaming
# ---------------------------------------------------------------------------

def _groq_post(msgs, model, stream=True, max_tokens=400):
    """POST to Groq (OpenAI-compatible). Returns (response, error_str)."""
    last_err = ""
    for attempt in range(3):
        try:
            return requests.post(
                GROQ_URL,
                json={"model": model, "messages": msgs,
                      "temperature": 0.75, "max_tokens": max_tokens, "stream": stream},
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                stream=stream,
                timeout=60,
            ), None
        except requests.RequestException as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(1)
    return None, last_err


# ---------------------------------------------------------------------------
# Multi-provider LLM dispatch
# ---------------------------------------------------------------------------

def _openai_compat_post(url, api_key, msgs, model, stream, max_tokens):
    """Generic OpenAI-compatible streaming/non-streaming POST."""
    last_err = ""
    for attempt in range(3):
        try:
            return requests.post(
                url,
                json={"model": model, "messages": msgs,
                      "temperature": 0.75, "max_tokens": max_tokens, "stream": stream},
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                stream=stream,
                timeout=60,
            ), None
        except requests.RequestException as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(1)
    return None, last_err


def _anthropic_stream(msgs, model, max_tokens):
    """Stream from Anthropic Claude API. Extracts system message if present."""
    if not ANTHROPIC_API_KEY:
        return None, "ANTHROPIC_API_KEY not set"
    system = ""
    chat = []
    for m in msgs:
        if m["role"] == "system":
            system = m["content"]
        else:
            chat.append(m)
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "stream": True,
        "messages": chat,
    }
    if system:
        body["system"] = system
    try:
        return requests.post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            stream=True,
            timeout=60,
        ), None
    except requests.RequestException as e:
        return None, str(e)


def _gemini_stream(msgs, model, max_tokens):
    """Stream from Google Gemini API."""
    if not GEMINI_API_KEY:
        return None, "GEMINI_API_KEY not set"
    system = ""
    contents = []
    for m in msgs:
        if m["role"] == "system":
            system = m["content"]
            continue
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    body = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.75},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    gem_model = model or "gemini-2.0-flash"
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{gem_model}:streamGenerateContent?key={GEMINI_API_KEY}&alt=sse")
    try:
        return requests.post(url, json=body, stream=True, timeout=60), None
    except requests.RequestException as e:
        return None, str(e)


def _ollama_stream(msgs, model, max_tokens):
    """Stream from local Ollama."""
    system = ""
    prompt_msgs = []
    for m in msgs:
        if m["role"] == "system":
            system = m["content"]
        else:
            prompt_msgs.append(m)
    body = {
        "model": model or OLLAMA_MODEL,
        "messages": ([{"role": "system", "content": system}] if system else []) + prompt_msgs,
        "stream": True,
        "options": {"num_predict": max_tokens},
    }
    try:
        return requests.post(
            f"{OLLAMA_URL}/api/chat", json=body, stream=True, timeout=120,
        ), None
    except requests.RequestException as e:
        return None, str(e)


def _iter_llm_tokens(resp, provider):
    """Yield text tokens from a streaming response for the given provider."""
    if provider in ("groq", "openai", "openrouter"):
        for line in resp.iter_lines():
            if not line or not line.startswith(b"data: "):
                continue
            data = line[6:]
            if data == b"[DONE]":
                break
            try:
                yield json.loads(data)["choices"][0]["delta"].get("content", "")
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
    elif provider == "anthropic":
        for line in resp.iter_lines():
            if not line or not line.startswith(b"data: "):
                continue
            data = line[6:]
            try:
                ev = json.loads(data)
                if ev.get("type") == "content_block_delta":
                    yield ev["delta"].get("text", "")
            except (json.JSONDecodeError, KeyError):
                pass
    elif provider == "gemini":
        for line in resp.iter_lines():
            if not line or not line.startswith(b"data: "):
                continue
            data = line[6:]
            try:
                ev = json.loads(data)
                parts = ev["candidates"][0]["content"]["parts"]
                yield parts[0].get("text", "")
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
    elif provider == "ollama":
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                ev = json.loads(line)
                yield ev.get("message", {}).get("content", "")
                if ev.get("done"):
                    break
            except (json.JSONDecodeError, KeyError):
                pass


def _llm_post(msgs, model, stream=True, max_tokens=400):
    """Route to the active LLM provider. Returns (resp_or_iter, error, provider)."""
    provider = LLM_SERVER

    if provider == "groq":
        resp, err = _groq_post(msgs, model, stream=stream, max_tokens=max_tokens)
        return resp, err, "groq"

    if provider == "openai":
        oai_model = model if model and not model.startswith("llama") else "gpt-4o-mini"
        resp, err = _openai_compat_post(
            "https://api.openai.com/v1/chat/completions",
            OPENAI_API_KEY, msgs, oai_model, stream, max_tokens,
        )
        return resp, err, "openai"

    if provider == "anthropic":
        ant_model = model if model and not model.startswith("llama") else "claude-haiku-4-5-20251001"
        resp, err = _anthropic_stream(msgs, ant_model, max_tokens)
        return resp, err, "anthropic"

    if provider == "gemini":
        gem_model = model if model and not model.startswith("llama") else "gemini-2.0-flash"
        resp, err = _gemini_stream(msgs, gem_model, max_tokens)
        return resp, err, "gemini"

    if provider == "ollama":
        resp, err = _ollama_stream(msgs, OLLAMA_MODEL, max_tokens)
        return resp, err, "ollama"

    if provider == "openrouter":
        or_model = model if model and not model.startswith("llama") else "meta-llama/llama-3.1-8b-instruct:free"
        resp, err = _openai_compat_post(
            "https://openrouter.ai/api/v1/chat/completions",
            OPENROUTER_API_KEY, msgs, or_model, stream, max_tokens,
        )
        return resp, err, "openrouter"

    # fallback to groq
    resp, err = _groq_post(msgs, model, stream=stream, max_tokens=max_tokens)
    return resp, err, "groq"


def _llm_complete(msgs, model, max_tokens=300, json_mode=False):
    """Non-streaming LLM call. Returns (text, error). Used for memory extraction etc."""
    provider = LLM_SERVER

    if provider in ("groq", "openai", "openrouter"):
        if provider == "groq":
            url, key = GROQ_URL, GROQ_API_KEY
        elif provider == "openai":
            url, key = "https://api.openai.com/v1/chat/completions", OPENAI_API_KEY
            model = model if model and not model.startswith("llama") else "gpt-4o-mini"
        else:
            url, key = "https://openrouter.ai/api/v1/chat/completions", OPENROUTER_API_KEY
            model = model if model and not model.startswith("llama") else "meta-llama/llama-3.1-8b-instruct:free"
        body = {"model": model, "messages": msgs,
                "temperature": 0.1, "max_tokens": max_tokens, "stream": False}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            r = requests.post(url, json=body,
                              headers={"Authorization": f"Bearer {key}"}, timeout=30)
            if r.status_code == 429:
                return None, "rate-limited"
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"], None
        except Exception as e:
            return None, str(e)

    if provider == "anthropic":
        resp, err = _anthropic_stream(msgs, model, max_tokens)
        if err:
            return None, err
        text = "".join(_iter_llm_tokens(resp, "anthropic"))
        return text, None

    if provider == "gemini":
        resp, err = _gemini_stream(msgs, model, max_tokens)
        if err:
            return None, err
        text = "".join(_iter_llm_tokens(resp, "gemini"))
        return text, None

    if provider == "ollama":
        resp, err = _ollama_stream(msgs, OLLAMA_MODEL, max_tokens)
        if err:
            return None, err
        text = "".join(_iter_llm_tokens(resp, "ollama"))
        return text, None

    return None, f"unknown provider: {provider}"


def _build_system_prompt(user_text, on_search=None):
    """Assemble system prompt: base + memory facts + RAG hits + optional web search."""
    parts = [SYSTEM_PROMPT]

    if USER_FACTS:
        facts_str = "\n".join(f"- {f}" for f in USER_FACTS[-20:])
        parts.append(f"\n\n## What I know about Ragnar:\n{facts_str}")

    with _notes_lock:
        notes_snapshot = list(USER_NOTES)
    if notes_snapshot:
        notes_str = "\n".join(f"- {n['text']}" for n in notes_snapshot[-30:])
        parts.append(f"\n\n## Ragnar's notes:\n{notes_str}")

    hits = retrieve_relevant(user_text)
    if hits:
        rag_lines = []
        for u, a in hits:
            rag_lines.append(f"User: {u[:300]}\nZeev: {a[:300]}")
        parts.append("\n\n## Relevant past exchanges:\n" + "\n---\n".join(rag_lines))

    if needs_torah(user_text):
        torah_hits = torah_search(user_text)
        if torah_hits:
            torah_lines = "\n".join(
                f"{ref}: {en[:500]}" for ref, en in torah_hits
            )
            parts.append(f"\n\n## Relevant Torah/Talmud passages:\n{torah_lines}")

    if needs_search(user_text) and TAVILY_API_KEY:
        if on_search:
            on_search(user_text)
        results = tavily_search(user_text)
        parts.append(f"\n\n[Web search results for '{user_text}']\n{results}")

    _LANG_INSTRUCTIONS = {
        "en": "Reply in English only.",
        "he": "Reply in Hebrew (עברית) only.",
        "es": "Reply in Spanish (Español) only.",
        "ru": "Reply in Russian (Русский) only.",
    }
    if FORCED_LANG and FORCED_LANG in _LANG_INSTRUCTIONS:
        parts.append(f"\n\n{_LANG_INSTRUCTIONS[FORCED_LANG]}")

    return "".join(parts)


# Keep old name as alias so nothing else breaks
def _with_search(user_text, on_search=None):
    return _build_system_prompt(user_text, on_search)


# ---------------------------------------------------------------------------
# Camera (Raspberry Pi NoIR Camera Module V2)
# ---------------------------------------------------------------------------

def init_camera():
    global CAMERA_AVAILABLE
    try:
        from picamera2 import Picamera2
        cam = Picamera2()
        cam.close()
        CAMERA_AVAILABLE = True
    except Exception:
        CAMERA_AVAILABLE = False


def init_thermal():
    global THERMAL_AVAILABLE
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        import mlx90640 as _t
        THERMAL_AVAILABLE = _t.init_thermal()
    except Exception:
        THERMAL_AVAILABLE = False

def init_mic():
    """Set WM8960 mic capture gain to levels tuned for clean recording."""
    try:
        subprocess.run(
            ["amixer", "-c", "wm8960soundcard", "sset", "Capture", "40"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["amixer", "-c", "wm8960soundcard", "sset", "ADC PCM", "220"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def capture_image(width=1280, height=720):
    """Capture a JPEG via picamera2. Returns base64 string or None."""
    try:
        from picamera2 import Picamera2
        cam = Picamera2()
        config = cam.create_still_configuration(main={"size": (width, height)})
        cam.configure(config)
        cam.start()
        time.sleep(0.5)  # let auto-exposure settle
        buf = io.BytesIO()
        cam.capture_file(buf, format="jpeg")
        cam.stop()
        cam.close()
        buf.seek(0)
        jpeg = buf.read()
        if CAMERA_FLIP:
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(jpeg)).rotate(180)
                out = io.BytesIO()
                img.save(out, format="jpeg")
                jpeg = out.getvalue()
            except Exception:
                pass
        return base64.b64encode(jpeg).decode()
    except Exception:
        return None


def _build_vision_msgs(image_b64, question=""):
    """Build the multimodal messages list for a vision API call."""
    q = question or "What do you see in this image? Describe it concisely."
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": q},
            ],
        },
    ]


def _rtl_print(text):
    """Print text in correct RTL visual order via fribidi, falling back to plain print."""
    if not text:
        return
    try:
        result = subprocess.run(
            ["fribidi", "--nopad"],
            input=text.encode(),
            capture_output=True,
            timeout=5,
        )
        print(result.stdout.decode().rstrip())
    except Exception:
        print(text)


def _screen_rows(text, first_line_prefix=0):
    """Count terminal screen rows occupied by text, accounting for line wrapping."""
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80
    import unicodedata
    def vlen(s):
        # Exclude zero-width combining characters (e.g. Hebrew nikud)
        return sum(1 for c in s if unicodedata.category(c) != 'Mn')
    segments = text.split('\n')
    rows = max(1, (first_line_prefix + vlen(segments[0]) + cols - 1) // cols)
    for seg in segments[1:]:
        rows += max(1, (vlen(seg) + cols - 1) // cols) if seg else 1
    return rows


def stream_reply(messages, model):
    if LLM_SERVER == "groq" and not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY environment variable is not set.")

    user_text = messages[-1]["content"] if messages else ""

    def on_search(q):
        print(f"{DIM}[searching: {q}]{RESET}", flush=True)

    sys_prompt = _build_system_prompt(user_text, on_search)
    payload_msgs = [{"role": "system", "content": sys_prompt}] + messages

    if needs_torah(user_text):
        tok_limit = 1200
    elif model in (MODELS["3"][0], MODELS["2"][0]):  # R1 or 70B
        tok_limit = 1200
    else:
        tok_limit = 600

    resp, err, provider = _llm_post(payload_msgs, model, max_tokens=tok_limit)
    if err:
        print(f"\nConnection error: {err}")
        return ""

    print(f"\n{CYAN}{BOLD}Zeev:{RESET} ", end="", flush=True)
    full = ""
    for delta in _iter_llm_tokens(resp, provider):
        if delta:
            full += delta
            print(delta, end="", flush=True)

    has_hebrew = any('֐' <= c <= '׿' for c in full)
    if has_hebrew and shutil.which("fribidi"):
        n = _screen_rows(full, first_line_prefix=len("Zeev: "))
        print(f"\n\033[{n}A\033[0J", end="", flush=True)
        print(f"{CYAN}{BOLD}Zeev:{RESET}")
        _rtl_print(full)
    else:
        print()
    return full


# ---------------------------------------------------------------------------
# Web interface HTML
# ---------------------------------------------------------------------------

_WEB_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Zeev</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #0d0d0d; color: #e8e8e8;
  font-family: system-ui, -apple-system, sans-serif;
  height: 100dvh; display: flex; flex-direction: column;
  -webkit-text-size-adjust: 100%;
}
header {
  padding: 12px 16px; border-bottom: 1px solid #1e1e1e;
  display: flex; align-items: center; justify-content: space-between;
  flex-shrink: 0; background: #111;
}
.brand { font-weight: 700; color: #7dd3fc; font-size: 1.1rem; letter-spacing: 0.04em; }
.controls { display: flex; align-items: center; gap: 10px; }
select {
  background: #1a1a1a; color: #e8e8e8; border: 1px solid #333;
  padding: 5px 8px; border-radius: 8px; font-size: 0.8rem; cursor: pointer;
}
#chat {
  flex: 1; overflow-y: auto; padding: 16px;
  display: flex; flex-direction: column; gap: 10px;
  scroll-behavior: smooth;
}
.bubble {
  max-width: 82%; padding: 10px 14px; border-radius: 18px;
  line-height: 1.55; font-size: 0.97rem; word-break: break-word;
}
.user-bubble {
  align-self: flex-end; background: #1d4ed8; color: #fff;
  border-bottom-right-radius: 5px;
}
.zeev-bubble {
  align-self: flex-start; background: #181818; color: #e8e8e8;
  border: 1px solid #252525; border-bottom-left-radius: 5px;
}
.zeev-bubble.pending { color: #555; }
footer {
  padding: 10px 12px; border-top: 1px solid #1e1e1e;
  display: flex; gap: 8px; flex-shrink: 0; background: #111;
  padding-bottom: max(10px, env(safe-area-inset-bottom));
}
#inp {
  flex: 1; background: #1a1a1a; color: #e8e8e8;
  border: 1px solid #2e2e2e; border-radius: 22px;
  padding: 10px 16px; font-size: 1rem; outline: none;
}
#inp:focus { border-color: #7dd3fc; }
.btn {
  border: none; border-radius: 50%; width: 42px; height: 42px;
  cursor: pointer; font-size: 1.1rem; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  transition: background 0.15s;
}
#sendBtn { background: #1d4ed8; color: #fff; }
#sendBtn:disabled { background: #222; cursor: not-allowed; }
#micBtn { background: #1e1e1e; color: #e8e8e8; border: 1px solid #333; }
#micBtn.active { background: #dc2626; border-color: #dc2626; }
#recBtn { background: #1e1e1e; color: #e8e8e8; border: 1px solid #333; }
#recBtn.active { background: #dc2626; border-color: #dc2626; animation: pulse 1s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.6} }
#snapBtn { background: #1e1e1e; color: #e8e8e8; border: 1px solid #333; }
#snapBtn:disabled { opacity: 0.4; cursor: not-allowed; }
#thermalBtn { background: #1e1e1e; color: #e8e8e8; border: 1px solid #333; }
#thermalBtn:disabled { opacity: 0.4; cursor: not-allowed; }
#flipBtn { background: #1e1e1e; color: #e8e8e8; border: 1px solid #333; }
#flipBtn.active { background: #1a3a2a; border-color: #2d6a4f; color: #52b788; }
.thermal-canvas { display: block; border-radius: 6px; margin-bottom: 6px; image-rendering: pixelated; }
.icon-btn {
  background: none; border: none; cursor: pointer; font-size: 1.1rem;
  color: #7dd3fc; padding: 4px; line-height: 1;
}
.model-tag {
  display: block; margin-top: 5px;
  font-size: 0.7rem; color: #3a3a3a; letter-spacing: 0.03em;
}
#battPct {
  font-size: 0.78rem; font-weight: 600; padding: 2px 4px;
  letter-spacing: 0.02em;
}
/* Memory overlay */
#memOverlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,0.85); z-index: 100;
  padding: 20px; overflow-y: auto;
}
#memPanel {
  background: #141414; border: 1px solid #252525; border-radius: 14px;
  padding: 20px; max-width: 480px; margin: 0 auto;
}
#memPanel h2 { color: #7dd3fc; font-size: 1rem; margin-bottom: 14px; }
#memList { font-size: 0.88rem; line-height: 1.9; color: #ccc; min-height: 40px; }
#memList em { color: #444; }
.mem-actions {
  display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap;
}
.mem-btn {
  padding: 7px 14px; border: none; border-radius: 8px;
  font-size: 0.85rem; cursor: pointer;
}
#memorizeBtn { background: #1d4ed8; color: #fff; }
#memorizeBtn:disabled { background: #1a2a4a; color: #556; cursor: not-allowed; }
#memCloseBtn { background: #1e1e1e; color: #aaa; border: 1px solid #333; }
/* Notes overlay */
#noteOverlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,0.85); z-index: 100;
  padding: 20px; overflow-y: auto;
}
#notePanel {
  background: #141414; border: 1px solid #252525; border-radius: 14px;
  padding: 20px; max-width: 480px; margin: 0 auto;
}
#notePanel h2 { color: #7dd3fc; font-size: 1rem; margin-bottom: 14px; }
#noteList { font-size: 0.88rem; line-height: 1.9; color: #ccc; min-height: 40px; }
#noteList em { color: #444; }
.note-row { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 4px; }
.note-row span { flex: 1; }
.note-del {
  background: none; border: none; color: #555; cursor: pointer;
  font-size: 0.9rem; padding: 0 2px; line-height: 1.9; flex-shrink: 0;
}
.note-del:hover { color: #ef4444; }
.note-add-row {
  display: flex; gap: 8px; margin-top: 16px; align-items: center;
}
#noteInp {
  flex: 1; background: #1a1a1a; color: #e8e8e8;
  border: 1px solid #2e2e2e; border-radius: 10px;
  padding: 8px 12px; font-size: 0.9rem; outline: none;
}
#noteInp:focus { border-color: #7dd3fc; }
#noteMicBtn {
  background: #1e1e1e; border: 1px solid #333; border-radius: 50%;
  width: 36px; height: 36px; cursor: pointer; font-size: 1rem;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}
#noteMicBtn.active { background: #dc2626; border-color: #dc2626; }
#noteAddBtn { background: #1d4ed8; color: #fff; }
#noteCloseBtn { background: #1e1e1e; color: #aaa; border: 1px solid #333; }
</style>
</head>
<body>

<!-- Memory overlay -->
<div id="memOverlay">
  <div id="memPanel">
    <h2>&#129504; Memory</h2>
    <div id="memList"><em>Loading...</em></div>
    <div class="mem-actions">
      <button class="mem-btn" id="memorizeBtn">Memorize this session</button>
      <button class="mem-btn" id="memCloseBtn">Close</button>
    </div>
  </div>
</div>

<!-- Notes overlay -->
<div id="noteOverlay">
  <div id="notePanel">
    <h2>&#128221; Notes</h2>
    <div id="noteList"><em>Loading...</em></div>
    <div class="note-add-row">
      <input id="noteInp" type="text" placeholder="New note…" dir="auto" />
      <button id="noteMicBtn" title="Dictate note">&#127908;</button>
      <button class="mem-btn" id="noteAddBtn">Add</button>
      <button class="mem-btn" id="noteCloseBtn">Close</button>
    </div>
  </div>
</div>

<header>
  <span class="brand">Zeev</span>
  <div class="controls">
    <span id="battPct" style="display:none"></span>
    <button class="icon-btn" id="noteBtn" title="Notes">&#128221;</button>
    <button class="icon-btn" id="memBtn" title="Memory">&#129504;</button>
    <button class="icon-btn" id="ttsBtn" title="Toggle speech">&#128266;</button>
    <button class="icon-btn" id="volDownBtn" title="Volume down">&#128265;</button>
    <span id="volDisplay" style="font-size:0.75rem;color:#aaa;min-width:2.5rem;text-align:center">75%</span>
    <button class="icon-btn" id="volUpBtn" title="Volume up">&#128266;</button>
    <button class="icon-btn" id="clearBtn" title="Clear session">&#128465;</button>
    <select id="modelSel">
      <option value="auto" selected>Auto</option>
      <option value="llama-3.1-8b-instant">8B Fast</option>
      <option value="llama-3.3-70b-versatile">70B Smart</option>
      <option value="deepseek-r1-distill-llama-70b">DeepSeek R1</option>
    </select>
  </div>
</header>
<div id="chat"></div>
<footer>
  <button class="btn" id="micBtn" title="Voice input">&#127908;</button>
  <button class="btn" id="recBtn" title="Hold to record, tap stop to send">&#9210;</button>
  <button class="btn" id="snapBtn" title="Take photo">&#128247;</button>
  <button class="btn" id="flipBtn" title="Toggle camera flip" style="display:none">&#8597;</button>
  <button class="btn" id="thermalBtn" title="Thermal camera" style="display:none">&#127777;</button>
  <input id="inp" type="text" placeholder="Message Zeev…" autocomplete="off" enterkeyhint="send" dir="auto" />
  <button class="btn" id="sendBtn" title="Send">&#10148;</button>
</footer>
<script>
"use strict";

const chat = document.getElementById("chat");
const inp = document.getElementById("inp");
const sendBtn = document.getElementById("sendBtn");
const micBtn = document.getElementById("micBtn");
const snapBtn = document.getElementById("snapBtn");
const ttsBtn = document.getElementById("ttsBtn");
const volDownBtn = document.getElementById("volDownBtn");
const volUpBtn = document.getElementById("volUpBtn");
const volDisplay = document.getElementById("volDisplay");
const clearBtn = document.getElementById("clearBtn");
const modelSel = document.getElementById("modelSel");
const memBtn = document.getElementById("memBtn");
const memOverlay = document.getElementById("memOverlay");
const memList = document.getElementById("memList");
const memorizeBtn = document.getElementById("memorizeBtn");
const memCloseBtn = document.getElementById("memCloseBtn");
const thermalBtn = document.getElementById("thermalBtn");
const flipBtn = document.getElementById("flipBtn");

fetch("/camera").then(r=>r.json()).then(d=>{
  if(!d.available) { snapBtn.style.display="none"; }
  else { flipBtn.style.display=""; }
}).catch(()=>{snapBtn.style.display="none";});
fetch("/thermal-status").then(r=>r.json()).then(d=>{if(d.available)thermalBtn.style.display="";}).catch(()=>{});
fetch("/camera-flip").then(r=>r.json()).then(d=>{if(d.flip)flipBtn.classList.add("active");}).catch(()=>{});

let ttsOn = true;
let busy = false;
let pendingModel = null;
let currentAudio = null;

const LANG_BCP47 = {en: "en-US", es: "es-MX", he: "he-IL", ru: "ru-RU"};

function cancelSpeech() {
  if (currentAudio) { currentAudio.pause(); URL.revokeObjectURL(currentAudio.src); currentAudio = null; }
  speechSynthesis.cancel();
}

function _speakBrowser(text, lang) {
  const u = new SpeechSynthesisUtterance(text.trim());
  u.lang = LANG_BCP47[lang] || "en-US";
  u.rate = 1.05;
  speechSynthesis.speak(u);
}

async function speak(text) {
  if (!ttsOn || !text.trim()) return;
  cancelSpeech();
  try {
    const res = await fetch("/tts", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text}),
    });
    if (res.ok) {
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      currentAudio = new Audio(url);
      currentAudio.onended = () => { URL.revokeObjectURL(url); currentAudio = null; };
      currentAudio.play();
    } else {
      // Groq TTS unavailable (non-English) — fall back to browser speechSynthesis
      const data = await res.json().catch(() => ({}));
      _speakBrowser(text, data.lang || "en");
    }
  } catch (_) { _speakBrowser(text, "en"); }
}

ttsBtn.onclick = () => {
  ttsOn = !ttsOn;
  ttsBtn.innerHTML = ttsOn ? "&#128266;" : "&#128263;";
  if (!ttsOn) cancelSpeech();
};

// --- Volume ---
async function fetchVolume() {
  try {
    const r = await fetch("/volume");
    const d = await r.json();
    volDisplay.textContent = d.volume + "%";
  } catch (_) {}
}
async function adjustVolume(delta) {
  try {
    const r = await fetch("/volume", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({delta}),
    });
    const d = await r.json();
    volDisplay.textContent = d.volume + "%";
  } catch (_) {}
}
volDownBtn.onclick = () => adjustVolume(-10);
volUpBtn.onclick   = () => adjustVolume(10);
fetchVolume();

// --- UI helpers ---
function addBubble(role, text) {
  const d = document.createElement("div");
  d.className = "bubble " + (role === "user" ? "user-bubble" : "zeev-bubble");
  d.dir = "auto";
  if (!text) d.classList.add("pending");
  d.textContent = text || "⋯";
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
  return d;
}

// --- Send ---
async function send(msg) {
  msg = msg.trim();
  if (!msg || busy) return;
  busy = true;
  sendBtn.disabled = true;
  inp.value = "";

  addBubble("user", msg);
  const zd = addBubble("zeev", "");

  cancelSpeech();
  pendingModel = null;

  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: msg, model: modelSel.value}),
    });

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let full = "";

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const lines = buf.split("\\n");
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const d = line.slice(6);
        if (d === "[DONE]") continue;
        try {
          const p = JSON.parse(d);
          if (p.token) {
            zd.classList.remove("pending");
            full += p.token;
            zd.textContent = full;
            chat.scrollTop = chat.scrollHeight;
          } else if (p.error) {
            zd.classList.remove("pending");
            zd.textContent = "Error: " + p.error;
          } else if (p.info) {
            zd.classList.remove("pending");
            zd.textContent = p.info;
          } else if (p.model) {
            pendingModel = p.model;
          }
        } catch (_) {}
      }
    }
    speak(full);
    if (!full && !zd.textContent) zd.textContent = "(no response)";
    if (pendingModel && full) {
      const tag = document.createElement("span");
      tag.className = "model-tag";
      tag.textContent = pendingModel;
      zd.appendChild(tag);
    }
  } catch (e) {
    zd.classList.remove("pending");
    zd.textContent = "Connection error.";
  }

  busy = false;
  sendBtn.disabled = false;
  inp.focus();
}

sendBtn.onclick = () => send(inp.value);
inp.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(inp.value); }
});

// --- Camera / snap ---
async function snap() {
  if (busy) return;
  busy = true;
  sendBtn.disabled = true;
  snapBtn.disabled = true;

  const question = inp.value.trim();
  inp.value = "";

  addBubble("user", question || "📷 What do you see?");
  const zd = addBubble("zeev", "");
  cancelSpeech();
  pendingModel = null;

  let full = "";
  let hasImage = false;
  let textSpan = null;

  try {
    const res = await fetch("/snap", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question}),
    });

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const lines = buf.split("\\n");
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const d = line.slice(6);
        if (d === "[DONE]") continue;
        try {
          const p = JSON.parse(d);
          if (p.image) {
            zd.classList.remove("pending");
            const img = document.createElement("img");
            img.src = p.image;
            img.style.cssText = "max-width:100%;border-radius:8px;margin-bottom:6px;display:block;";
            zd.insertBefore(img, zd.firstChild);
            hasImage = true;
          } else if (p.token) {
            zd.classList.remove("pending");
            full += p.token;
            if (!textSpan) { textSpan = document.createElement("span"); zd.appendChild(textSpan); }
            textSpan.textContent = full;
            chat.scrollTop = chat.scrollHeight;
          } else if (p.error) {
            zd.classList.remove("pending");
            zd.textContent = "Error: " + p.error;
          } else if (p.info && !hasImage) {
            zd.classList.remove("pending");
            zd.textContent = p.info;
          } else if (p.model) {
            pendingModel = p.model;
          }
        } catch (_) {}
      }
    }
    speak(full);
    if (pendingModel && full) {
      const tag = document.createElement("span");
      tag.className = "model-tag";
      tag.textContent = pendingModel;
      zd.appendChild(tag);
    }
  } catch (e) {
    zd.classList.remove("pending");
    zd.textContent = "Connection error.";
  }

  busy = false;
  sendBtn.disabled = false;
  snapBtn.disabled = false;
  inp.focus();
}

snapBtn.onclick = snap;

flipBtn.onclick = async () => {
  const res = await fetch("/flip", {method: "POST"});
  const d = await res.json();
  flipBtn.classList.toggle("active", d.flip);
  flipBtn.title = d.flip ? "Camera flip ON (click to disable)" : "Toggle camera flip";
};

// --- Thermal camera ---
const _THERMAL_PALETTE = [
  [0,0,200],[0,180,220],[0,200,80],[220,200,0],[230,80,0],[230,0,0]
];
function _thermalColor(t) {
  t = Math.max(0, Math.min(1, t));
  const seg = t * (_THERMAL_PALETTE.length - 1);
  const lo = Math.floor(seg), hi = Math.min(lo+1, _THERMAL_PALETTE.length-1);
  const f = seg - lo;
  const [r0,g0,b0] = _THERMAL_PALETTE[lo], [r1,g1,b1] = _THERMAL_PALETTE[hi];
  return [r0+f*(r1-r0), g0+f*(g1-g0), b0+f*(b1-b0)];
}
function renderThermalCanvas(frame, summary) {
  const SCALE = 8, W = 32, H = 24;
  const canvas = document.createElement("canvas");
  canvas.width = W * SCALE; canvas.height = H * SCALE;
  canvas.className = "thermal-canvas";
  const ctx2 = canvas.getContext("2d");
  const img = ctx2.createImageData(W * SCALE, H * SCALE);
  const span = summary.max - summary.min || 1;
  for (let row = 0; row < H; row++) {
    for (let col = 0; col < W; col++) {
      const t = (frame[row*W+col] - summary.min) / span;
      const [r,g,b] = _thermalColor(t);
      for (let dy = 0; dy < SCALE; dy++) {
        for (let dx = 0; dx < SCALE; dx++) {
          const i = ((row*SCALE+dy)*W*SCALE + col*SCALE+dx) * 4;
          img.data[i]=r; img.data[i+1]=g; img.data[i+2]=b; img.data[i+3]=255;
        }
      }
    }
  }
  ctx2.putImageData(img, 0, 0);
  // hotspot crosshair
  ctx2.strokeStyle = "white"; ctx2.lineWidth = 1.5;
  const hx = (summary.hotspot_col + 0.5) * SCALE, hy = (summary.hotspot_row + 0.5) * SCALE;
  ctx2.beginPath(); ctx2.moveTo(hx-6,hy); ctx2.lineTo(hx+6,hy); ctx2.moveTo(hx,hy-6); ctx2.lineTo(hx,hy+6); ctx2.stroke();
  return canvas;
}

async function thermal() {
  if (busy) return;
  busy = true;
  sendBtn.disabled = true;
  thermalBtn.disabled = true;

  const question = inp.value.trim();
  inp.value = "";

  addBubble("user", question || "🌡 Thermal scan");
  const zd = addBubble("zeev", "");
  cancelSpeech();
  pendingModel = null;

  let full = "", textSpan = null;
  try {
    const res = await fetch("/thermal", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question}),
    });
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const lines = buf.split("\\n"); buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const d = line.slice(6); if (d === "[DONE]") continue;
        try {
          const p = JSON.parse(d);
          if (p.thermal) {
            zd.classList.remove("pending");
            const canvas = renderThermalCanvas(p.thermal.frame, p.thermal);
            zd.insertBefore(canvas, zd.firstChild);
            const statsEl = document.createElement("div");
            statsEl.style.cssText = "font-size:0.75rem;color:#888;margin-bottom:4px;";
            statsEl.textContent = `Min ${p.thermal.min}°C  Max ${p.thermal.max}°C  Center ${p.thermal.center}°C`;
            zd.insertBefore(statsEl, canvas.nextSibling);
          } else if (p.token) {
            zd.classList.remove("pending");
            full += p.token;
            if (!textSpan) { textSpan = document.createElement("span"); zd.appendChild(textSpan); }
            textSpan.textContent = full;
            chat.scrollTop = chat.scrollHeight;
          } else if (p.error) {
            zd.classList.remove("pending"); zd.textContent = "Error: " + p.error;
          } else if (p.info) {
            if (!full) { zd.classList.remove("pending"); zd.textContent = p.info; }
          } else if (p.model) { pendingModel = p.model; }
        } catch (_) {}
      }
    }
    if (full) speak(full);
    if (pendingModel && full) {
      const tag = document.createElement("span"); tag.className = "model-tag"; tag.textContent = pendingModel; zd.appendChild(tag);
    }
  } catch(e) { zd.classList.remove("pending"); zd.textContent = "Connection error."; }

  busy = false; sendBtn.disabled = false; thermalBtn.disabled = false; inp.focus();
}
thermalBtn.onclick = thermal;

clearBtn.onclick = async () => {
  await fetch("/clear", {method: "POST"});
  chat.innerHTML = "";
  cancelSpeech();
};

// --- Memory UI ---
function renderFacts(facts) {
  if (!facts || facts.length === 0) {
    memList.innerHTML = "<em>No facts stored yet. Chat a while, then tap 'Memorize'.</em>";
  } else {
    memList.innerHTML = facts.map((f, i) =>
      `<div><span style="color:#444">${i+1}.</span> ${f}</div>`
    ).join("");
  }
}

memBtn.onclick = async () => {
  memOverlay.style.display = "block";
  memList.innerHTML = "<em>Loading...</em>";
  try {
    const res = await fetch("/memory");
    const data = await res.json();
    renderFacts(data.facts);
  } catch (_) {
    memList.innerHTML = "<em style='color:#c00'>Failed to load memory.</em>";
  }
};

memCloseBtn.onclick = () => { memOverlay.style.display = "none"; };
memOverlay.addEventListener("click", e => {
  if (e.target === memOverlay) memOverlay.style.display = "none";
});

memorizeBtn.onclick = async () => {
  memorizeBtn.textContent = "Memorizing…";
  memorizeBtn.disabled = true;
  try {
    const res = await fetch("/memorize", {method: "POST"});
    const data = await res.json();
    renderFacts(data.facts);
  } catch (_) {
    memList.innerHTML = "<em style='color:#c00'>Memorization failed.</em>";
  }
  memorizeBtn.textContent = "Memorize this session";
  memorizeBtn.disabled = false;
};

// --- Speech recognition (shared) ---
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

// --- Notes UI ---
const noteBtn = document.getElementById("noteBtn");
const noteOverlay = document.getElementById("noteOverlay");
const noteList = document.getElementById("noteList");
const noteInp = document.getElementById("noteInp");
const noteAddBtn = document.getElementById("noteAddBtn");
const noteCloseBtn = document.getElementById("noteCloseBtn");
const noteMicBtn = document.getElementById("noteMicBtn");

function renderNotes(notes) {
  if (!notes || notes.length === 0) {
    noteList.innerHTML = "<em>No notes yet. Type one below or tap the mic.</em>";
    return;
  }
  noteList.innerHTML = notes.map((n, i) => {
    const ts = (n.ts || "").slice(0, 10);
    const safe = n.text.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    return `<div class="note-row"><span><span style="color:#444">${i+1}.</span> <span style="color:#555;font-size:0.78rem">${ts}</span> ${safe}</span><button class="note-del" data-idx="${i}" title="Delete">&#10005;</button></div>`;
  }).join("");
  noteList.querySelectorAll(".note-del").forEach(btn => {
    btn.onclick = async () => {
      const idx = parseInt(btn.dataset.idx);
      const res = await fetch("/delete-note", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({idx})});
      const d = await res.json();
      renderNotes(d.notes);
    };
  });
}

noteBtn.onclick = async () => {
  noteOverlay.style.display = "block";
  noteList.innerHTML = "<em>Loading...</em>";
  try {
    const res = await fetch("/notes");
    const d = await res.json();
    renderNotes(d.notes);
  } catch (_) {
    noteList.innerHTML = "<em style='color:#c00'>Failed to load notes.</em>";
  }
  noteInp.focus();
};

noteCloseBtn.onclick = () => { noteOverlay.style.display = "none"; };
noteOverlay.addEventListener("click", e => {
  if (e.target === noteOverlay) noteOverlay.style.display = "none";
});

async function submitNote() {
  const text = noteInp.value.trim();
  if (!text) return;
  noteInp.value = "";
  try {
    const res = await fetch("/note", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text})});
    const d = await res.json();
    renderNotes(d.notes);
  } catch (_) {}
}

noteAddBtn.onclick = submitNote;
noteInp.addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); submitNote(); }
});

if (SR) {
  const noteRec = new SR();
  noteRec.continuous = false;
  noteRec.interimResults = false;
  noteRec.lang = "en-US";
  noteRec.onresult = e => {
    noteInp.value = e.results[0][0].transcript;
    noteMicBtn.classList.remove("active");
  };
  noteRec.onend = () => noteMicBtn.classList.remove("active");
  noteRec.onerror = () => noteMicBtn.classList.remove("active");
  noteMicBtn.onclick = () => {
    if (noteMicBtn.classList.contains("active")) { noteRec.stop(); }
    else { noteMicBtn.classList.add("active"); noteRec.start(); }
  };
} else {
  noteMicBtn.style.display = "none";
}

// --- Battery ---
const battPct = document.getElementById("battPct");
async function updateBattery() {
  try {
    const res = await fetch("/battery");
    const d = await res.json();
    if (d.level === null) { battPct.style.display = "none"; return; }
    const pct = Math.round(d.level);
    battPct.textContent = (d.charging ? "⚡" : "🔋") + " " + pct + "%";
    battPct.style.color = pct < 20 ? "#ef4444" : pct < 50 ? "#eab308" : "#4ade80";
    battPct.style.display = "";
  } catch (_) { battPct.style.display = "none"; }
}
updateBattery();
setInterval(updateBattery, 30000);

// --- Chat speech recognition (tap-to-speak, auto-send on silence) ---
if (SR) {
  const rec = new SR();
  rec.continuous = false;
  rec.interimResults = false;
  rec.lang = "en-US";
  rec.onresult = e => { send(e.results[0][0].transcript); };
  rec.onend = () => micBtn.classList.remove("active");
  rec.onerror = () => micBtn.classList.remove("active");
  micBtn.onclick = () => {
    if (micBtn.classList.contains("active")) { rec.stop(); }
    else { micBtn.classList.add("active"); rec.start(); }
  };
} else {
  micBtn.style.display = "none";
}

// --- Continuous recording (tap start → record until tap stop → transcribe & send) ---
const recBtn = document.getElementById("recBtn");
let mediaRec = null;
let recChunks = [];

recBtn.onclick = async () => {
  if (mediaRec && mediaRec.state === "recording") {
    mediaRec.stop();
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    addBubble("assistant", "Microphone access denied.");
    return;
  }
  const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus"
    : MediaRecorder.isTypeSupported("audio/ogg;codecs=opus")
      ? "audio/ogg;codecs=opus"
      : "";
  mediaRec = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
  recChunks = [];
  mediaRec.ondataavailable = e => { if (e.data.size > 0) recChunks.push(e.data); };
  mediaRec.onstop = async () => {
    recBtn.classList.remove("active");
    stream.getTracks().forEach(t => t.stop());
    const blob = new Blob(recChunks, { type: mediaRec.mimeType || "audio/webm" });
    recChunks = [];
    const statusDiv = addBubble("assistant", "Transcribing…");
    try {
      const resp = await fetch("/transcribe", {
        method: "POST",
        headers: { "Content-Type": blob.type },
        body: blob,
      });
      const data = await resp.json();
      statusDiv.remove();
      if (data.transcript) { send(data.transcript); }
      else { addBubble("assistant", "Could not transcribe: " + (data.error || "unknown error")); }
    } catch (err) {
      statusDiv.remove();
      addBubble("assistant", "Transcription request failed.");
    }
  };
  mediaRec.start();
  recBtn.classList.add("active");
};
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------

def _local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def _ensure_cert(cert_path, key_path, ip):
    """Generate a self-signed TLS cert with SAN for the local IP if missing."""
    if cert_path.exists() and key_path.exists():
        return
    import subprocess
    cert_path.parent.mkdir(exist_ok=True)
    san = f"subjectAltName=IP:{ip},IP:127.0.0.1,DNS:localhost"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-days", "3650", "-nodes",
            "-subj", "/CN=zeev",
            "-addext", san,
        ],
        check=True,
        capture_output=True,
    )


def run_web_server(host="0.0.0.0", port=5000, use_https=False):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    zeev_cleanup()   # clear any crash leftovers from a previous run
    init_learning()
    init_camera()
    init_thermal()
    session = load_prior()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = _WEB_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/battery":
                level, charging = get_battery()
                body = json.dumps({"level": level, "charging": charging}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/memory":
                body = json.dumps({"facts": USER_FACTS}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/camera":
                body = json.dumps({"available": CAMERA_AVAILABLE}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/camera-flip":
                body = json.dumps({"flip": CAMERA_FLIP}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/thermal-status":
                body = json.dumps({"available": THERMAL_AVAILABLE}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/volume":
                body = json.dumps({"volume": _VOLUME}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/notes":
                with _notes_lock:
                    notes_data = list(USER_NOTES)
                body = json.dumps({"notes": notes_data}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/clear":
                with lock:
                    session.clear()
                self.send_response(204)
                self.end_headers()
                return

            if self.path == "/volume":
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length)) if length else {}
                if "delta" in data:
                    new_vol = set_volume(_VOLUME + int(data["delta"]))
                elif "level" in data:
                    new_vol = set_volume(int(data["level"]))
                else:
                    new_vol = _VOLUME
                body = json.dumps({"volume": new_vol}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/flip":
                global CAMERA_FLIP
                CAMERA_FLIP = not CAMERA_FLIP
                save_settings()
                body = json.dumps({"flip": CAMERA_FLIP}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/tts":
                length = int(self.headers.get("Content-Length", 0))
                try:
                    data = json.loads(self.rfile.read(length))
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return
                text = data.get("text", "")
                lang = detect_lang(text)
                audio, ctype = tts_web(text, lang)
                if audio:
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(audio)))
                    self.end_headers()
                    self.wfile.write(audio)
                else:
                    # Last resort: tell the browser to use speechSynthesis
                    body = json.dumps({"lang": lang}).encode()
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                return

            if self.path == "/note":
                length = int(self.headers.get("Content-Length", 0))
                try:
                    data = json.loads(self.rfile.read(length))
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return
                text = data.get("text", "").strip()
                if text:
                    add_note(text)
                with _notes_lock:
                    notes_data = list(USER_NOTES)
                body = json.dumps({"notes": notes_data}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/delete-note":
                length = int(self.headers.get("Content-Length", 0))
                try:
                    data = json.loads(self.rfile.read(length))
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return
                idx = data.get("idx", -1)
                delete_note(idx)
                with _notes_lock:
                    notes_data = list(USER_NOTES)
                body = json.dumps({"notes": notes_data}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/memorize":
                with lock:
                    snap = list(session)
                facts = extract_memory(snap)
                body = json.dumps({"facts": facts}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/transcribe":
                length = int(self.headers.get("Content-Length", 0))
                audio_bytes = self.rfile.read(length) if length else b""
                if not audio_bytes:
                    body = json.dumps({"error": "no audio"}).encode()
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                # Web audio may be webm/ogg — convert to WAV for local STT providers,
                # or forward raw to cloud Whisper endpoints that accept webm.
                content_type = self.headers.get("Content-Type", "audio/webm")
                ext = "webm" if "webm" in content_type else "ogg" if "ogg" in content_type else "wav"
                try:
                    if STT_SERVER in ("vosk", "faster-whisper") or ext == "wav":
                        # Convert to WAV via ffmpeg for local providers
                        import tempfile, os as _os
                        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
                            f.write(audio_bytes); tmp_in = f.name
                        tmp_out = tmp_in.replace(f".{ext}", ".wav")
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", tmp_in, "-ar", "16000", "-ac", "1",
                             "-f", "wav", tmp_out],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                        if Path(tmp_out).exists():
                            wav_bytes = Path(tmp_out).read_bytes()
                            _os.unlink(tmp_in); _os.unlink(tmp_out)
                        else:
                            wav_bytes = audio_bytes
                        transcript = stt(wav_bytes)
                    else:
                        # Cloud providers can handle webm directly via multipart POST
                        stt_url = GROQ_STT_URL if STT_SERVER == "groq" else "https://api.openai.com/v1/audio/transcriptions"
                        stt_key = GROQ_API_KEY if STT_SERVER == "groq" else OPENAI_API_KEY
                        stt_model = "whisper-large-v3-turbo" if STT_SERVER == "groq" else OPENAI_STT_MODEL
                        boundary = b"--boundary\r\n"
                        cd = f'Content-Disposition: form-data; name="file"; filename="audio.{ext}"\r\n'.encode()
                        ct = f"Content-Type: {content_type}\r\n\r\n".encode()
                        model_part = (
                            b"--boundary\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n"
                            + stt_model.encode() + b"\r\n"
                        )
                        multipart = boundary + cd + ct + audio_bytes + b"\r\n" + model_part + b"--boundary--\r\n"
                        r = requests.post(
                            stt_url,
                            headers={"Authorization": f"Bearer {stt_key}",
                                     "Content-Type": "multipart/form-data; boundary=boundary"},
                            data=multipart, timeout=30,
                        )
                        transcript = r.json().get("text", "").strip() if r.status_code == 200 else ""
                    body = json.dumps({"transcript": transcript}).encode()
                except Exception as e:
                    body = json.dumps({"error": str(e)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/thermal":
                length = int(self.headers.get("Content-Length", 0))
                try:
                    data = json.loads(self.rfile.read(length)) if length else {}
                except json.JSONDecodeError:
                    data = {}
                question = data.get("question", "").strip()

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                def thermal_sse(obj):
                    self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
                    self.wfile.flush()

                if not THERMAL_AVAILABLE:
                    thermal_sse({"error": "Thermal camera not available"})
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return

                try:
                    import sys as _sys
                    _sys.path.insert(0, str(BASE_DIR))
                    import mlx90640 as _t
                    frame   = _t.capture_frame()
                    summary = _t.frame_summary(frame)
                except Exception as e:
                    thermal_sse({"error": f"Thermal read failed: {e}"})
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return

                thermal_sse({"thermal": {**summary, "frame": frame}})

                if question:
                    ctx = (f"[thermal camera] Min {summary['min']}°C, Max {summary['max']}°C, "
                           f"Center {summary['center']}°C, hotspot at row {summary['hotspot_row']} "
                           f"col {summary['hotspot_col']} (32×24 grid). {question}")
                    model_id = route_model(question)
                    thermal_sse({"model": model_label(model_id)})

                    def on_search(q):
                        thermal_sse({"info": f"[searching: {q}]"})

                    sys_prompt   = _build_system_prompt(ctx, on_search)
                    with lock:
                        snapshot = list(session) + [{"role": "user", "content": ctx}]
                    payload = [{"role": "system", "content": sys_prompt}] + snapshot
                    thermal_reply = ""
                    try:
                        resp, err = _groq_post(payload, model_id)
                        if err:
                            thermal_sse({"error": err})
                        else:
                            for line in resp.iter_lines():
                                if not line or not line.startswith(b"data: "):
                                    continue
                                chunk = line[6:]
                                if chunk == b"[DONE]":
                                    break
                                try:
                                    delta = json.loads(chunk)["choices"][0]["delta"].get("content", "")
                                    if delta:
                                        thermal_reply += delta
                                        thermal_sse({"token": delta})
                                except (json.JSONDecodeError, KeyError, IndexError):
                                    pass
                    except requests.RequestException as e:
                        thermal_sse({"error": str(e)})

                    if thermal_reply:
                        with lock:
                            session.append({"role": "user", "content": ctx})
                            session.append({"role": "assistant", "content": thermal_reply})
                            append_message("user", ctx)
                            append_message("assistant", thermal_reply)
                            if len(session) > 60:
                                session[:] = session[-60:]

                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return

            if self.path == "/snap":
                length = int(self.headers.get("Content-Length", 0))
                try:
                    data = json.loads(self.rfile.read(length)) if length else {}
                except json.JSONDecodeError:
                    data = {}
                question = data.get("question", "").strip()

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                def snap_sse(obj):
                    self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
                    self.wfile.flush()

                if not CAMERA_AVAILABLE:
                    snap_sse({"error": "Camera not available"})
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return

                snap_sse({"info": "Capturing…"})
                img = capture_image()
                if not img:
                    snap_sse({"error": "Capture failed"})
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return

                snap_sse({"image": f"data:image/jpeg;base64,{img}"})
                snap_sse({"model": "Scout"})

                vision_payload = _build_vision_msgs(img, question)
                snap_reply = ""
                try:
                    resp, err = _groq_post(vision_payload, VISION_MODEL)
                    if err:
                        snap_sse({"error": err})
                    else:
                        for line in resp.iter_lines():
                            if not line or not line.startswith(b"data: "):
                                continue
                            chunk = line[6:]
                            if chunk == b"[DONE]":
                                break
                            try:
                                delta = json.loads(chunk)["choices"][0]["delta"].get("content", "")
                                if delta:
                                    snap_reply += delta
                                    snap_sse({"token": delta})
                            except (json.JSONDecodeError, KeyError, IndexError):
                                pass
                except requests.RequestException as e:
                    snap_sse({"error": str(e)})

                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

                if snap_reply:
                    q_text = question or "What do you see?"
                    with lock:
                        session.append({"role": "user", "content": f"[camera] {q_text}"})
                        session.append({"role": "assistant", "content": snap_reply})
                        append_message("user", f"[camera] {q_text}")
                        append_message("assistant", snap_reply)
                        if len(session) > 60:
                            session[:] = session[-60:]
                return

            if self.path != "/chat":
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return

            user_msg   = data.get("message", "").strip()
            model_pref = data.get("model", "auto")
            model      = route_model(user_msg) if model_pref == "auto" else model_pref

            if not user_msg:
                self.send_response(400)
                self.end_headers()
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def sse(obj):
                self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
                self.wfile.flush()

            if not GROQ_API_KEY:
                sse({"error": "GROQ_API_KEY not set on the server"})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return

            with lock:
                session.append({"role": "user", "content": user_msg})
                append_message("user", user_msg)
                snapshot = list(session)

            if model_pref == "auto":
                sse({"model": model_label(model)})

            def on_search(q):
                sse({"info": f"[searching: {q}]"})

            sys_prompt = _build_system_prompt(user_msg, on_search)
            payload_msgs = [{"role": "system", "content": sys_prompt}] + snapshot
            if needs_torah(user_msg):
                tok_limit = 1200
            elif model in (MODELS["3"][0], MODELS["2"][0]):  # R1 or 70B
                tok_limit = 1200
            else:
                tok_limit = 600
            full_reply = ""
            try:
                resp, err = _groq_post(payload_msgs, model, max_tokens=tok_limit)
                if err:
                    sse({"error": err})
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return

                for line in resp.iter_lines():
                    if not line or not line.startswith(b"data: "):
                        continue
                    chunk = line[6:]
                    if chunk == b"[DONE]":
                        break
                    try:
                        delta = json.loads(chunk)["choices"][0]["delta"].get("content", "")
                        if delta:
                            full_reply += delta
                            sse({"token": delta})
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass

            except requests.RequestException as e:
                sse({"error": str(e)})

            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

            if full_reply:
                with lock:
                    session.append({"role": "assistant", "content": full_reply})
                    append_message("assistant", full_reply)
                    if len(session) > 60:
                        session[:] = session[-60:]

    ip = _local_ip()
    server = ThreadingHTTPServer((host, port), Handler)

    if use_https:
        import ssl
        cert = BASE_DIR / "data" / "cert.pem"
        key  = BASE_DIR / "data" / "key.pem"
        _ensure_cert(cert, key, ip)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    else:
        scheme = "http"

    print(f"\n{BOLD}Zeev Web{RESET} — AI Companion")
    print(f"{DIM}Open in Chrome on your phone:{RESET}")
    print(f"  {CYAN}{scheme}://{ip}:{port}/{RESET}")
    if use_https:
        print(f"{DIM}  (accept the self-signed cert warning once){RESET}")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        zeev_cleanup()
        print(f"\n{DIM}Shutting down.{RESET}\n")


# ---------------------------------------------------------------------------
# Wake word listener
# ---------------------------------------------------------------------------
# openwakeword (preferred) with RMS fallback.
# Set WAKE_WORD_ENABLED=true in .env to activate openwakeword.
# Install: pip install openwakeword  (also needs numpy and sox)
# Models: set WAKE_WORD_MODELS=hey_jarvis or a comma-sep list of model paths/names.
# Without WAKE_WORD_ENABLED the legacy RMS keyword listener is used.
# ---------------------------------------------------------------------------

_WAKE_WORDS = {"listen", "hey listen"}   # legacy fallback


def _rms(wav_bytes):
    """Return RMS amplitude of raw 16-bit LE PCM (skipping the 44-byte WAV header)."""
    import struct, math
    pcm = wav_bytes[44:]
    if len(pcm) < 2:
        return 0
    samples = struct.unpack_from(f"<{len(pcm)//2}h", pcm)
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def _record_chunk(duration=2.0, device="plughw:wm8960soundcard,0"):
    """Record `duration` seconds of audio and return WAV bytes, or None on error."""
    try:
        r = subprocess.run(
            ["arecord", "-D", device, "-f", "S16_LE", "-r", "16000", "-c", "1",
             "-t", "wav", "-d", str(int(duration)), "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        return r.stdout if r.stdout else None
    except Exception:
        return None


def _record_utterance(device="plughw:wm8960soundcard,0", max_seconds=8,
                      silence_threshold=None, silence_run=8):
    """Record until silence_run consecutive silent 0.25s chunks or max_seconds elapsed.
    Returns WAV bytes or None."""
    import wave, io

    if silence_threshold is None:
        floor_vals = []
        for _ in range(2):
            w = _record_chunk(0.25, device)
            if w:
                floor_vals.append(_rms(w))
        silence_threshold = int(max(floor_vals) * 1.3) if floor_vals else 3000

    chunks_pcm = []
    silent = 0
    total = 0
    chunk_dur = 0.25
    max_chunks = int(max_seconds / chunk_dur)

    for _ in range(max_chunks):
        wav = _record_chunk(chunk_dur, device)
        if not wav:
            break
        pcm = wav[44:]
        chunks_pcm.append(pcm)
        total += 1
        rms = _rms(wav)
        if rms < silence_threshold:
            silent += 1
            if silent >= silence_run and total > silence_run:
                break
        else:
            silent = 0

    if not chunks_pcm:
        return None

    raw = b"".join(chunks_pcm)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(raw)
    return buf.getvalue()


def _play_beep(device="plughw:wm8960soundcard,0"):
    """Play a short 880 Hz acknowledgment beep."""
    try:
        import struct, math, wave as _wave, io as _io
        sr, dur, freq = 16000, 0.12, 880
        samples = [int(28000 * math.sin(2 * math.pi * freq * i / sr))
                   for i in range(int(sr * dur))]
        raw = struct.pack(f"<{len(samples)}h", *samples)
        buf = _io.BytesIO()
        with _wave.open(buf, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
            wf.writeframes(raw)
        subprocess.run(
            ["aplay", "-D", device, "-q", "-"],
            input=buf.getvalue(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _openwakeword_available():
    try:
        import openwakeword  # noqa: F401
        import numpy          # noqa: F401
        return shutil.which("sox") is not None
    except ImportError:
        return False


def _start_openwakeword_listener(voice_queue, stop_event, device="plughw:wm8960soundcard,0"):
    """Launch openwakeword as a subprocess (via sox), emit voice events on detection."""
    import importlib.util, os as _os

    wakeword_script = Path(__file__).parent / "wakeword.py"

    env = dict(_os.environ)
    if WAKE_WORD_MODELS:
        env["WAKE_WORDS"] = WAKE_WORD_MODELS
    env["WAKE_WORD_THRESHOLD"] = str(WAKE_WORD_THRESHOLD)
    env["WAKE_WORD_COOLDOWN_SEC"] = str(WAKE_WORD_COOLDOWN)

    def _run():
        try:
            proc = subprocess.Popen(
                [sys.executable, str(wakeword_script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            buf = ""
            while not stop_event.is_set():
                line_bytes = proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode().strip()
                if line.startswith("WAKE"):
                    _play_beep(device)
                    follow_wav = _record_utterance(device)
                    if follow_wav:
                        transcript = stt(follow_wav)
                        if transcript and transcript.strip():
                            voice_queue.put(("voice", transcript.strip()))
                elif line == "[WakeWord] READY":
                    pass  # model loaded OK
            try:
                proc.terminate()
            except Exception:
                pass
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def _start_rms_listener(voice_queue, stop_event, device="plughw:wm8960soundcard,0"):
    """Legacy RMS-threshold keyword listener (fallback when openwakeword unavailable)."""

    def _run():
        _noise_floor = []
        for _ in range(3):
            w = _record_chunk(1.0, device)
            if w:
                _noise_floor.append(_rms(w))
        rms_gate = int(max(_noise_floor) * 1.5) if _noise_floor else 3000

        while not stop_event.is_set():
            wav = _record_chunk(2.0, device)
            if not wav or stop_event.is_set():
                continue
            if _rms(wav) < rms_gate:
                continue

            text = stt(wav)
            if not text:
                continue

            low = text.lower().strip()
            triggered = any(w in low for w in _WAKE_WORDS)
            if not triggered:
                continue

            utterance_after_wake = low
            for w in sorted(_WAKE_WORDS, key=len, reverse=True):
                if utterance_after_wake.startswith(w):
                    utterance_after_wake = utterance_after_wake[len(w):].lstrip(" ,.")
                    break

            _play_beep(device)

            if len(utterance_after_wake) > 2:
                voice_queue.put(("voice", utterance_after_wake))
                continue

            follow_wav = _record_utterance(device)
            if follow_wav:
                follow_text = stt(follow_wav)
                if follow_text and follow_text.strip():
                    voice_queue.put(("voice", follow_text.strip()))

    threading.Thread(target=_run, daemon=True).start()


def start_keyword_listener(voice_queue, stop_event, device="plughw:wm8960soundcard,0"):
    """Start the best available wake-word listener.
    Uses openwakeword subprocess if WAKE_WORD_ENABLED=true and deps are present;
    otherwise falls back to the legacy RMS keyword listener."""
    wakeword_script = Path(__file__).parent / "wakeword.py"
    if WAKE_WORD_ENABLED and wakeword_script.exists() and _openwakeword_available():
        _start_openwakeword_listener(voice_queue, stop_event, device)
    else:
        _start_rms_listener(voice_queue, stop_event, device)


# ---------------------------------------------------------------------------
# Terminal REPL
# ---------------------------------------------------------------------------

def pick_model(current_locked=None):
    """Interactive model picker. Returns model_id to lock, or None for auto."""
    print(f"{DIM}Select model:{RESET}")
    auto_tag = " (current)" if current_locked is None else ""
    print(f"  0) Auto  — smart routing{auto_tag}")
    for key, (mid, label) in MODELS.items():
        tag = " (current)" if mid == current_locked else ""
        print(f"  {key}) {label}{tag}")
    while True:
        choice = input(f"{DIM}[0]:{RESET} ").strip() or "0"
        if choice == "0":
            print(f"{DIM}Auto-routing enabled.{RESET}\n")
            return None
        if choice in MODELS:
            model_id, label = MODELS[choice]
            print(f"{DIM}Locked to {label.split('—')[0].strip()}.{RESET}\n")
            return model_id
        print(f"{DIM}Enter 0, 1, 2, or 3.{RESET}")


def run_device_mode():
    """Push-to-talk voice companion using the Whisplay HAT (LCD + WM8960 + button)."""
    zeev_cleanup()   # clear any crash leftovers from a previous run
    sys.path.insert(0, str(Path.home() / "Whisplay" / "runtime"))
    try:
        from whisplay import WhisplayBoard
    except ImportError:
        sys.exit("Whisplay runtime not found. Clone https://github.com/PiSugar/Whisplay to ~/Whisplay")

    try:
        from PIL import Image, ImageDraw, ImageFont
        _have_pil = True
    except ImportError:
        _have_pil = False
        print("python3-pillow not found — LCD display disabled. Install with: sudo apt install python3-pillow")

    init_learning()
    init_tts()

    # Lower speaker volume to 75%
    try:
        subprocess.run(
            ["amixer", "-c", "wm8960soundcard", "sset", "Speaker", "120"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    init_mic()

    board  = WhisplayBoard()
    # 100 MHz SPI is too fast for Pi Zero 2W — reinit at a reliable speed
    board.spi.max_speed_hz = 10_000_000
    board._reset_lcd()
    board._init_display()
    session = load_prior()

    W, H = 240, 280
    _FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    _FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    # ── LCD helpers ──────────────────────────────────────────────────────────

    def _push_lcd(img):
        pixels = list(img.convert("RGB").getdata())
        buf = bytearray(len(pixels) * 2)
        for i, (r, g, b) in enumerate(pixels):
            v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            buf[i * 2]     = v >> 8
            buf[i * 2 + 1] = v & 0xFF
        board.draw_image(0, 0, W, H, bytes(buf))

    def _load_font(path, size):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            return ImageFont.load_default()

    def _wrap_text(draw, text, font, max_w):
        words = text.split()
        lines, line = [], []
        for w in words:
            test = " ".join(line + [w])
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > max_w and line:
                lines.append(" ".join(line))
                line = [w]
            else:
                line.append(w)
        if line:
            lines.append(" ".join(line))
        return lines

    # ── Animated face ────────────────────────────────────────────────────────
    # Face: circle centred at (120, 118), radius 90 → leaves 52 px below for caption

    _FACE_CX, _FACE_CY, _FACE_R = 120, 118, 90

    _STATE_COLORS = {
        "idle":      (50,  120, 220),
        "ready":     (0,   160, 255),
        "listening": (0,   210, 230),
        "thinking":  (210, 155,  10),
        "speaking":  (40,  210,  90),
        "error":     (220,  60,  60),
    }

    _face_state   = "idle"
    _face_caption = ""          # short text shown below the face
    _mouth_open   = False
    _face_lock    = threading.Lock()

    def _draw_face_img(state, mouth_open, caption):
        img  = Image.new("RGB", (W, H), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        col  = _STATE_COLORS.get(state, (120, 120, 120))

        cx, cy, r = _FACE_CX, _FACE_CY, _FACE_R

        # Face circle
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(22, 28, 45), outline=col, width=4)

        # Eyes
        for ex, ey in [(cx-30, cy-22), (cx+30, cy-22)]:
            draw.ellipse([ex-13, ey-13, ex+13, ey+13], fill=(235, 235, 255))
            if state == "thinking":
                # pupils look up-right
                draw.ellipse([ex+1, ey-8, ex+9, ey+1], fill=(18, 18, 30))
            elif state == "listening":
                # wide pupils, centred
                draw.ellipse([ex-7, ey-7, ex+7, ey+7], fill=(18, 18, 30))
            else:
                draw.ellipse([ex-5, ey-5, ex+5, ey+5], fill=(18, 18, 30))

            # Eyebrows
            brow_y = ey - 18
            if state == "listening":
                draw.line([ex-13, brow_y+4, ex+13, brow_y-2], fill=col, width=3)
            elif state == "thinking":
                draw.line([ex-13, brow_y-2, ex+13, brow_y+4], fill=col, width=3)
            else:
                draw.line([ex-13, brow_y, ex+13, brow_y], fill=col, width=2)

        # Mouth
        mouth_y = cy + 28
        if state == "listening":
            # small 'o'
            draw.ellipse([cx-8, mouth_y-6, cx+8, mouth_y+10], outline=(210, 150, 160), width=2)
        elif state in ("speaking",) and mouth_open:
            # open oval
            draw.ellipse([cx-22, mouth_y-5, cx+22, mouth_y+20], fill=(160, 50, 60))
            draw.ellipse([cx-22, mouth_y-5, cx+22, mouth_y+5],  fill=(210, 110, 120))
        else:
            # gentle smile arc
            draw.arc([cx-26, mouth_y-8, cx+26, mouth_y+22],
                     start=15, end=165, fill=(210, 150, 160), width=3)

        # Caption below face
        if caption and _have_pil:
            font_sm = _load_font(_FONT_PATH, 13)
            lines   = _wrap_text(draw, caption, font_sm, W - 16)
            y       = cy + r + 8
            for ln in lines[:3]:
                bbox = draw.textbbox((0, 0), ln, font=font_sm)
                tw   = bbox[2] - bbox[0]
                draw.text(((W - tw) // 2, y), ln, font=font_sm, fill=(200, 200, 200))
                y += 16
                if y > H - 4:
                    break

        # State label at very bottom
        font_lbl = _load_font(_FONT_PATH, 12)
        labels   = {"idle": "Press to start", "ready": "Your turn",
                    "listening": "Listening...", "thinking": "Thinking...",
                    "speaking": "Speaking...", "error": "Error"}
        label    = labels.get(state, state)
        bbox     = draw.textbbox((0, 0), label, font=font_lbl)
        tw       = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, H - 16), label, font=font_lbl, fill=col)

        return img

    def _set_face(state, caption=""):
        nonlocal _face_state, _face_caption
        with _face_lock:
            _face_state   = state
            _face_caption = caption

    def _face_loop():
        while True:
            now = time.time()
            with _face_lock:
                state   = _face_state
                caption = _face_caption
            if _have_pil:
                try:
                    from face_scroll import draw_frame
                    img = draw_frame(state, caption, now, batt=get_battery())
                    _push_lcd(img)
                except Exception as e:
                    print(f"LCD error: {e}")
            time.sleep(0.08)

    threading.Thread(target=_face_loop, daemon=True).start()

    # ── TTS (interruptible, blocking) ────────────────────────────────────────

    _tts_p1 = None
    _tts_p2 = None
    _piper_dev_proc = None   # persistent piper process — kept alive between utterances

    def _collect_piper_audio(p, timeout=5.0):
        """Read raw PCM from a live piper process until it goes quiet (line done)."""
        audio = bytearray()
        fd = p.stdout.fileno()
        while True:
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                break
            try:
                chunk = os.read(fd, 16384)
            except OSError:
                break
            if not chunk:
                break
            audio.extend(chunk)
        return bytes(audio)

    def _drain_piper(p):
        """Discard any buffered piper output (e.g. after an interrupt)."""
        fd = p.stdout.fileno()
        while True:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                break
            try:
                if not os.read(fd, 16384):
                    break
            except OSError:
                break

    def _interrupt_tts():
        nonlocal _tts_p1, _tts_p2, _piper_dev_proc
        for p in (_tts_p2, _tts_p1):
            if p:
                try:
                    p.terminate()
                except Exception:
                    pass
        # If piper was killed (p1 == _piper_dev_proc), mark it dead so next
        # call restarts it cleanly rather than writing to a dead process.
        if _tts_p1 is not None and _tts_p1 is _piper_dev_proc:
            _piper_dev_proc = None
        elif _piper_dev_proc and _piper_dev_proc.poll() is None:
            # piper survived (wasn't tracked as p1 this call) — drain its buffer
            _drain_piper(_piper_dev_proc)

    def _speak_device(text):
        nonlocal _tts_p1, _tts_p2, _piper_dev_proc
        lang = detect_lang(text)
        clean = _clean_for_tts(text, lang)
        if not clean:
            return

        # 1. Cloud TTS — dispatched by active provider (Orpheus/OpenAI for en, gTTS otherwise)
        wav = groq_tts(clean) if (TTS_SERVER in ("auto", "orpheus") and lang == "en") else (
              _openai_tts(clean, lang) if (TTS_SERVER == "openai" and lang == "en") else None)
        if wav:
            try:
                p2 = subprocess.Popen(
                    ["aplay", "-D", "plughw:wm8960soundcard,0", "-q", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                _tts_p1, _tts_p2 = None, p2
                try:
                    p2.stdin.write(wav)
                    p2.stdin.close()
                except BrokenPipeError:
                    pass
                p2.wait()
                return
            except Exception as e:
                print(f"Groq TTS playback error: {e}")
            finally:
                _tts_p1 = _tts_p2 = None

        # 2. Google Translate TTS — non-English (fast cloud, no model load lag)
        _GTTS_LANGS = {"he": "he", "es": "es", "ru": "ru"}
        if lang in _GTTS_LANGS and shutil.which("mpg123"):
            for chunk in _gtts_chunks(clean):
                mp3 = _gtts_fetch_chunk(chunk, _GTTS_LANGS[lang])
                if mp3:
                    p1 = subprocess.Popen(
                        ["mpg123", "-q", "-a", "plughw:wm8960soundcard,0", "-"],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    _tts_p1 = p1
                    try:
                        p1.stdin.write(mp3)
                        p1.stdin.close()
                    except BrokenPipeError:
                        pass
                    p1.wait()
            return

        # 3. Piper — persistent local neural TTS (model stays loaded between calls)
        model = (PIPER_MODELS.get(lang) or PIPER_MODELS.get("en")) if PIPER_BIN else None
        try:
            if model:
                # Reuse or (re)start the persistent piper process.
                if _piper_dev_proc is None or _piper_dev_proc.poll() is not None:
                    _piper_dev_proc = subprocess.Popen(
                        [PIPER_BIN, "--model", model, "--output_raw"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
                p1 = _piper_dev_proc
                _tts_p1, _tts_p2 = p1, None
                # Write one line — piper synthesises, then waits for the next line.
                p1.stdin.write(clean.encode() + b"\n")
                p1.stdin.flush()
                audio = _collect_piper_audio(p1)
                if audio:
                    p2 = subprocess.Popen(
                        ["aplay", "-D", "plughw:wm8960soundcard,0",
                         "-r", "22050", "-f", "S16_LE", "-t", "raw", "-q", "-"],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    _tts_p2 = p2
                    try:
                        p2.stdin.write(audio)
                        p2.stdin.close()
                    except BrokenPipeError:
                        pass
                    p2.wait()
            else:
                # 4. espeak-ng — last resort
                espeak_lang = {"he": "he", "es": "es", "ru": "ru"}.get(lang, "en")
                p1 = subprocess.Popen(
                    ["espeak-ng", "-s", "145", "-v", espeak_lang, "--stdout", clean],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
                p2 = subprocess.Popen(
                    ["aplay", "-D", "plughw:wm8960soundcard,0",
                     "-f", "S16_LE", "-r", "22050", "-t", "raw", "-c", "1", "-q", "-"],
                    stdin=p1.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                p1.stdout.close()
                _tts_p1, _tts_p2 = p1, p2
                p2.wait()
                p1.wait()
        except Exception as e:
            print(f"TTS error: {e}")
        finally:
            _tts_p1 = _tts_p2 = None

    # ── Recording state ──────────────────────────────────────────────────────
    # States: idle → listening → thinking → speaking → ready → listening → …
    #   idle    : no active session
    #   ready   : session active, waiting for button press to speak
    #   listening : recording
    #   thinking  : STT + LLM in flight
    #   speaking  : Zeev talking
    #
    # Button semantics (consistent throughout):
    #   idle/ready + press   → start recording
    #   listening  + release → stop recording, send
    #   listening  + press   → cancel turn, end session → idle
    #   speaking   + press   → interrupt TTS → ready
    #   thinking   + press   → ignored

    _rec_proc   = None
    _busy       = threading.Event()   # set while a session is active
    _state_lock = threading.Lock()
    _rec_file   = Path("/tmp/zeev_rec.wav")

    _LED_IDLE      = (0,  20,  0)
    _LED_READY     = (0,  40, 80)
    _LED_RECORDING = (180, 0,  0)
    _LED_THINKING  = (0,   0, 180)
    _LED_SPEAKING  = (0, 180, 50)
    _LED_ERROR     = (150, 0,  0)

    def _go_ready():
        _set_face("ready")
        board.set_rgb(*_LED_READY)

    def _go_idle():
        _set_face("idle")
        board.set_rgb(*_LED_IDLE)
        _busy.clear()

    def _start_recording():
        nonlocal _rec_proc
        board.set_rgb(*_LED_RECORDING)
        _set_face("listening")
        _rec_file.unlink(missing_ok=True)
        try:
            _rec_proc = subprocess.Popen(
                ["arecord", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav", str(_rec_file)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(f"arecord error: {e}", flush=True)
            _set_face("error", "Mic error")
            board.set_rgb(*_LED_ERROR)
            _go_idle()

    def _on_press():
        nonlocal _rec_proc
        with _state_lock:
            current = _face_state
            if current in ("idle", "ready"):
                if not _busy.is_set():
                    _busy.set()
                _start_recording()
            elif current == "speaking":
                _interrupt_tts()         # _process will call _go_ready() when TTS exits
            elif current == "listening":
                # Cancel this turn and end the session
                proc, _rec_proc = _rec_proc, None
                if proc:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                _go_idle()
            # thinking: ignore

    def _on_release():
        nonlocal _rec_proc, session
        if _face_state != "listening":
            return

        proc, _rec_proc = _rec_proc, None
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

        board.set_rgb(*_LED_THINKING)
        _set_face("thinking")

        def _process():
            nonlocal session
            t0 = time.perf_counter()

            try:
                wav = _rec_file.read_bytes() if _rec_file.exists() else b""
            except Exception:
                wav = b""

            if len(wav) < 1000:
                _set_face("error", "No audio captured")
                board.set_rgb(*_LED_ERROR)
                time.sleep(2)
                _go_ready() if _busy.is_set() else _go_idle()
                return

            print(f"[+{time.perf_counter()-t0:.1f}s] STT…", flush=True)
            transcript = stt(wav)
            print(f"[+{time.perf_counter()-t0:.1f}s] STT ({STT_SERVER}): {transcript!r}", flush=True)

            if not transcript:
                _set_face("error", "Didn't catch that")
                board.set_rgb(*_LED_ERROR)
                time.sleep(2)
                _go_ready() if _busy.is_set() else _go_idle()
                return

            print(f"You: {transcript}")
            _set_face("thinking", transcript)

            session.append({"role": "user", "content": transcript})
            append_message("user", transcript)

            model_id = route_model(transcript)
            short    = _MODEL_SHORT.get(model_id, "?")

            print(f"[+{time.perf_counter()-t0:.1f}s] LLM [{short}]…", flush=True)
            sys_prompt   = _build_system_prompt(transcript)
            payload_msgs = [{"role": "system", "content": sys_prompt}] + session
            resp, err    = _groq_post(payload_msgs, model_id, stream=False)
            print(f"[+{time.perf_counter()-t0:.1f}s] LLM done", flush=True)

            if err or not resp or resp.status_code != 200:
                detail = err or (resp.text[:80] if resp else "no response")
                print(f"LLM error: {detail}", flush=True)
                _set_face("error", "LLM error")
                board.set_rgb(*_LED_ERROR)
                time.sleep(2)
                _go_ready() if _busy.is_set() else _go_idle()
                return

            reply = resp.json()["choices"][0]["message"]["content"].strip()
            print(f"Zeev [{short}]: {reply}\n")
            session.append({"role": "assistant", "content": reply})
            append_message("assistant", reply)

            if len(session) > 60:
                session = session[-60:]

            board.set_rgb(*_LED_SPEAKING)
            _set_face("speaking", reply)
            print(f"[+{time.perf_counter()-t0:.1f}s] Speaking…", flush=True)
            _speak_device(reply)
            print(f"[+{time.perf_counter()-t0:.1f}s] Done", flush=True)

            # After speaking (normally or interrupted): go ready if still in session
            _go_ready() if _busy.is_set() else _go_idle()

        threading.Thread(target=_process, daemon=True).start()

    board.on_button_press(_on_press)
    board.on_button_release(_on_release)
    board.set_backlight(100)
    board.set_rgb(*_LED_IDLE)
    _set_face("idle")

    turns = len(session) // 2
    print(f"\n{BOLD}Zeev Device Mode{RESET} — Whisplay HAT", flush=True)
    if turns:
        print(f"{DIM}({turns} prior turn{'s' if turns != 1 else ''} loaded){RESET}", flush=True)
    print(f"{DIM}Hold button to speak, release to send. Press during speaking to interrupt.")
    print(f"Press while recording to cancel & exit.")
    print(f"Keyboard: [Ctrl+Space] toggle record/send  [q] quit{RESET}\n", flush=True)

    _hour = time.localtime().tm_hour
    _tod = "morning" if _hour < 12 else "afternoon" if _hour < 18 else "evening"
    _greeting = f"Good {_tod}, Alex. Ready when you are."
    print(f"[startup] greeting: {_greeting!r}", flush=True)
    threading.Thread(target=_speak_device, args=(_greeting,), daemon=True).start()

    def _keyboard_listener():
        import tty, termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch == '\x00':  # Ctrl+Space — toggle record / send
                    if _face_state == "listening":
                        _on_release()
                    else:
                        _on_press()
                elif ch in ('q', 'Q', '\x03'):  # q or Ctrl-C
                    break
        except Exception:
            pass
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        board.cleanup()
        sys.exit(0)

    def _evdev_listener():
        try:
            import evdev
            from evdev import ecodes
        except ImportError:
            return

        def _find_keyboard():
            for path in evdev.list_devices():
                try:
                    dev = evdev.InputDevice(path)
                    caps = dev.capabilities()
                    if ecodes.EV_KEY in caps and ecodes.KEY_LEFTCTRL in caps[ecodes.EV_KEY]:
                        return dev
                except Exception:
                    pass
            return None

        while True:
            dev = _find_keyboard()
            if dev is None:
                time.sleep(2)
                continue
            try:
                for event in dev.read_loop():
                    if event.type == ecodes.EV_KEY and event.code == ecodes.KEY_LEFTCTRL:
                        if event.value == 1:    # key down
                            _on_press()
                        elif event.value == 0:  # key up
                            _on_release()
            except Exception:
                time.sleep(1)   # device disconnected — retry

    threading.Thread(target=_keyboard_listener, daemon=True).start()
    threading.Thread(target=_evdev_listener, daemon=True).start()

    def _shutdown(sig=None, frame=None):
        zeev_cleanup()
        board.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        time.sleep(1)


def main():
    global _SETTINGS_TTS_ON
    zeev_cleanup()   # clear any crash leftovers from a previous run
    init_learning()

    import queue as _queue
    _stop_listen = threading.Event()

    def shutdown(sig=None, frame=None):
        _stop_listen.set()
        print(f"\n{DIM}Goodbye.{RESET}\n")
        if shutil.which("mpg123"):
            mp3 = _gtts_fetch_chunk("Goodbye, Alex.", "en")
            if mp3:
                proc = subprocess.Popen(
                    ["mpg123", "-q", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.stdin.write(mp3)
                proc.stdin.close()
                proc.wait()
        zeev_cleanup()
        try:
            readline.write_history_file(str(RL_HISTORY))
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        readline.read_history_file(str(RL_HISTORY))
    except FileNotFoundError:
        pass
    readline.set_history_length(200)

    init_tts()
    _piper_warmup()
    init_camera()
    init_thermal()
    init_mic()

    print(f"\n{BOLD}Zeev v2.0{RESET} — AI Companion")
    print(f"{DIM}  /clear        wipe session context")
    print(f"  /forget       delete all history")
    print(f"  /model        switch model")
    print(f"  /memory       show stored facts")
    print(f"  /memorize     learn from this session")
    print(f"  /forget-fact  remove a fact by number")
    print(f"  /note <text>  save a persistent note")
    print(f"  /notes        list all notes")
    print(f"  /forget-note  remove a note by number")
    print(f"  /lang         set response language (auto/en/he/es/ru)")
    print(f"  /tts          toggle speech output")
    print(f"  /listen       record voice and send as message")
    print(f"  /vol [+/-/N]  show or adjust volume (0–100)")
    print(f"  /stop         stop music playback")
    print(f"  /bt           manage Bluetooth devices")
    print(f"  /look [q]     take a photo and ask Zeev about it")
    print(f"  /flip         toggle camera 180° rotation (persistent)")
    print(f"  /thermal [q]  capture thermal frame; optionally ask Zeev about it")
    print(f"  quit          exit{RESET}\n")

    locked_model = None   # None = auto-routing
    tts_on       = _SETTINGS_TTS_ON
    session      = load_prior()

    if session:
        turns = len(session) // 2
        print(f"{DIM}({turns} prior turn{'s' if turns != 1 else ''} loaded){RESET}", end="")
    if USER_FACTS:
        print(f"  {DIM}({len(USER_FACTS)} fact{'s' if len(USER_FACTS) != 1 else ''} in memory){RESET}", end="")
    batt_level, batt_charging = get_battery()
    if batt_level is not None:
        batt_icon = "⚡" if batt_charging else "🔋"
        print(f"  {DIM}{batt_icon} {batt_level:.0f}%{RESET}", end="")
    if session or USER_FACTS or batt_level is not None:
        print()
    print(f"{DIM}Model: auto-routing  (/model to change)  |  Language: auto  (/lang to change){RESET}\n")

    _hour = time.localtime().tm_hour
    _tod  = "morning" if _hour < 12 else "afternoon" if _hour < 18 else "evening"
    _greeting = f"Good {_tod}, Alex. Ready when you are."
    # Use gTTS for the greeting — much faster than Piper's ~20s cold-start on Pi Zero
    if shutil.which("mpg123"):
        _gtts_speak(_greeting, "en")
    else:
        speak_terminal(_greeting)

    # Start wake-word listener if mic is available
    _input_q = _queue.Queue()   # both keyboard and voice feed here
    _mic_device  = "plughw:wm8960soundcard,0"
    _has_mic     = bool(shutil.which("arecord") and GROQ_API_KEY)

    # Keyboard input thread — keeps readline/history working
    def _kbd_thread():
        while not _stop_listen.is_set():
            try:
                line = input(f"{BOLD}You:{RESET} ").strip()
                _input_q.put(("kbd", line))
            except (EOFError, KeyboardInterrupt):
                _input_q.put(("exit", ""))
                return
    threading.Thread(target=_kbd_thread, daemon=True).start()

    if _has_mic:
        print(f"{DIM}Mic ready — type /listen to record a voice message.{RESET}\n")
    else:
        print()

    while True:
        try:
            source, user_input = _input_q.get(timeout=0.3)
        except _queue.Empty:
            continue

        if source == "exit":
            break
        if source == "voice":
            print(f"\r{BOLD}You:{RESET} {DIM}[voice]{RESET} {user_input}")

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "bye"):
            if session:
                print(f"{DIM}Memorizing session...{RESET}", end=" ", flush=True)
                facts = extract_memory(session)
                if facts is None:
                    print(f"{DIM}(rate-limited, try /memorize later){RESET}")
                else:
                    print(f"{DIM}({len(facts)} fact{'s' if len(facts) != 1 else ''} stored){RESET}")
            print(f"\n{CYAN}Zeev:{RESET} Take care. I'll be here when you need me.\n")
            break

        if user_input.lower() == "/clear":
            session.clear()
            print(f"{DIM}Session context cleared.{RESET}\n")
            continue

        if user_input.lower() == "/forget":
            if HISTORY_FILE.exists():
                HISTORY_FILE.unlink()
            session.clear()
            print(f"{DIM}All history deleted.{RESET}\n")
            continue

        if user_input.lower() == "/model":
            locked_model = pick_model(locked_model)
            continue

        if user_input.lower().startswith("/lang"):
            global FORCED_LANG
            parts = user_input.split()
            _LANG_OPTS = {"auto": None, "en": "en", "he": "he", "es": "es", "ru": "ru"}
            if len(parts) == 2 and parts[1].lower() in _LANG_OPTS:
                FORCED_LANG = _LANG_OPTS[parts[1].lower()]
                label = parts[1].lower() if FORCED_LANG else "auto"
                print(f"{DIM}Language set to {label}.{RESET}\n")
            else:
                current = FORCED_LANG or "auto"
                print(f"{DIM}Current language: {current}")
                print(f"  /lang auto   auto-detect from your input")
                print(f"  /lang en     English")
                print(f"  /lang he     Hebrew (עברית)")
                print(f"  /lang es     Spanish (Español)")
                print(f"  /lang ru     Russian (Русский){RESET}\n")
            continue

        if user_input.lower() == "/memory":
            if USER_FACTS:
                print(f"{DIM}Stored facts:{RESET}")
                for i, f in enumerate(USER_FACTS, 1):
                    print(f"  {DIM}{i}.{RESET} {f}")
            else:
                print(f"{DIM}No facts stored yet.{RESET}")
            print()
            continue

        if user_input.lower() == "/memorize":
            if not session:
                print(f"{DIM}Nothing to memorize yet.{RESET}\n")
                continue
            print(f"{DIM}Memorizing...{RESET}", end=" ", flush=True)
            facts = extract_memory(session)
            if facts is None:
                print(f"{DIM}(rate-limited, try again in a moment){RESET}\n")
            else:
                print(f"{DIM}({len(facts)} fact{'s' if len(facts) != 1 else ''} stored){RESET}\n")
            continue

        if user_input.lower().startswith("/forget-fact"):
            parts = user_input.split()
            if len(parts) != 2 or not parts[1].isdigit():
                print(f"{DIM}Usage: /forget-fact <number>{RESET}\n")
                continue
            idx = int(parts[1]) - 1
            if 0 <= idx < len(USER_FACTS):
                removed = USER_FACTS.pop(idx)
                save_memory(USER_FACTS)
                print(f"{DIM}Removed: {removed}{RESET}\n")
            else:
                print(f"{DIM}No fact #{parts[1]}.{RESET}\n")
            continue

        if user_input.lower().startswith("/note "):
            text = user_input[6:].strip()
            if not text:
                print(f"{DIM}Usage: /note <text>{RESET}\n")
            else:
                add_note(text)
                print(f"{DIM}Note saved.{RESET}\n")
            continue

        if user_input.lower() == "/notes":
            if USER_NOTES:
                print(f"{DIM}Notes:{RESET}")
                for i, n in enumerate(USER_NOTES, 1):
                    ts = n.get("ts", "")[:10]
                    print(f"  {DIM}{i}.{RESET} [{ts}] {n['text']}")
            else:
                print(f"{DIM}No notes yet. Use /note <text> to save one.{RESET}")
            print()
            continue

        if user_input.lower().startswith("/forget-note"):
            parts = user_input.split()
            if len(parts) != 2 or not parts[1].isdigit():
                print(f"{DIM}Usage: /forget-note <number>{RESET}\n")
                continue
            idx = int(parts[1]) - 1
            if delete_note(idx):
                print(f"{DIM}Note removed.{RESET}\n")
            else:
                print(f"{DIM}No note #{parts[1]}.{RESET}\n")
            continue

        if user_input.lower().startswith("/vol"):
            arg = user_input[4:].strip()
            if arg in ("+", "up"):
                new_vol = set_volume(_VOLUME + 10)
            elif arg in ("-", "down"):
                new_vol = set_volume(_VOLUME - 10)
            elif arg.lstrip("-").isdigit():
                new_vol = set_volume(int(arg))
            else:
                print(f"{DIM}Volume: {_VOLUME}%{RESET}\n")
                continue
            print(f"{DIM}Volume: {new_vol}%{RESET}\n")
            continue

        if user_input.lower() == "/stop":
            if music_stop():
                print(f"{DIM}Music stopped.{RESET}\n")
            else:
                print(f"{DIM}Nothing playing.{RESET}\n")
            continue

        if user_input.lower() == "/tts":
            if not TTS_AVAILABLE:
                print(f"{DIM}No TTS engine found. Install Piper (recommended) or espeak-ng.")
                print(f"  Piper: download from github.com/rhasspy/piper and set PIPER_MODEL=/path/to/model.onnx")
                print(f"  espeak-ng: sudo apt install espeak-ng{RESET}\n")
                continue
            tts_on = not tts_on
            _SETTINGS_TTS_ON = tts_on
            save_settings()
            state = "on" if tts_on else "off"
            print(f"{DIM}Speech {state}.{RESET}\n")
            continue

        if user_input.lower() == "/flip":
            CAMERA_FLIP = not CAMERA_FLIP
            save_settings()
            state = "on (180° rotation)" if CAMERA_FLIP else "off"
            print(f"{DIM}Camera flip {state}.{RESET}\n")
            continue

        if user_input.lower() == "/listen":
            if not _has_mic:
                print(f"{DIM}Mic not available.{RESET}\n")
                continue
            print(f"{DIM}[recording... speak now]{RESET}", flush=True)
            wav = _record_utterance(_mic_device)
            if not wav:
                print(f"{DIM}Recording failed.{RESET}\n")
                continue
            transcript = groq_stt(wav)
            if not transcript or not transcript.strip():
                print(f"{DIM}(nothing heard){RESET}\n")
                continue
            user_input = transcript.strip()
            print(f"\r{BOLD}You:{RESET} {DIM}[voice]{RESET} {user_input}")
            # fall through to normal message handling

        if user_input.lower().startswith("/look"):
            if not CAMERA_AVAILABLE:
                print(f"{DIM}Camera not available.{RESET}\n")
                continue
            question = user_input[5:].strip()
            print(f"{DIM}[capturing...]{RESET}", flush=True)
            img = capture_image()
            if not img:
                print(f"{DIM}Capture failed.{RESET}\n")
                continue
            vision_msgs = _build_vision_msgs(img, question)
            resp, err = _groq_post(vision_msgs, VISION_MODEL, stream=True)
            if err:
                print(f"Error: {err}\n")
                continue
            print(f"\n{CYAN}{BOLD}Zeev:{RESET} ", end="", flush=True)
            full_reply = ""
            for line in resp.iter_lines():
                if not line or not line.startswith(b"data: "):
                    continue
                chunk = line[6:]
                if chunk == b"[DONE]":
                    break
                try:
                    delta = json.loads(chunk)["choices"][0]["delta"].get("content", "")
                    print(delta, end="", flush=True)
                    full_reply += delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass
            print()
            if full_reply:
                q_text = question or "What do you see?"
                session.append({"role": "user", "content": f"[camera] {q_text}"})
                session.append({"role": "assistant", "content": full_reply})
                append_message("user", f"[camera] {q_text}")
                append_message("assistant", full_reply)
                if tts_on:
                    speak_terminal(full_reply)
            continue

        if user_input.lower().startswith("/thermal"):
            if not THERMAL_AVAILABLE:
                print(f"{DIM}Thermal camera not available.{RESET}\n")
                continue
            question = user_input[8:].strip()
            print(f"{DIM}[reading thermal frame...]{RESET}", flush=True)
            try:
                import sys as _sys
                _sys.path.insert(0, str(Path(__file__).parent))
                import mlx90640 as _t
                frame = _t.capture_frame()
                summary = _t.frame_summary(frame)
            except Exception as e:
                print(f"{DIM}Thermal read failed: {e}{RESET}\n")
                continue
            print(_t.ascii_map(frame))
            print(f"{DIM}Min: {summary['min']}°C  Max: {summary['max']}°C  "
                  f"Center: {summary['center']}°C  "
                  f"Hotspot: row {summary['hotspot_row']}, col {summary['hotspot_col']}{RESET}\n")
            if question:
                ctx = (f"[thermal camera] Min {summary['min']}°C, Max {summary['max']}°C, "
                       f"Center {summary['center']}°C, hotspot at row {summary['hotspot_row']} "
                       f"col {summary['hotspot_col']} (32×24 grid). {question}")
                model_id = locked_model if locked_model else route_model(question)
                if locked_model is None:
                    print(f"{DIM}[auto → {model_label(model_id)}]{RESET}", flush=True)
                session.append({"role": "user", "content": ctx})
                append_message("user", ctx)
                reply = stream_reply(session, model_id)
                if reply:
                    session.append({"role": "assistant", "content": reply})
                    append_message("assistant", reply)
                    if tts_on:
                        speak_terminal(reply)
                if len(session) > 60:
                    session = session[-60:]
            continue

        if user_input.lower().startswith("/bt"):
            parts = user_input.split()
            devices = bt_list()
            if not devices:
                print(f"{DIM}No paired Bluetooth devices found.")
                print(f"Run setup_bluetooth.sh to pair a device.{RESET}\n")
                continue
            if len(parts) == 1:
                # show status
                print(f"{DIM}Paired devices:{RESET}")
                for i, (mac, name, connected) in enumerate(devices, 1):
                    dot = f"{CYAN}●{RESET}" if connected else f"{DIM}○{RESET}"
                    status = f"{DIM} connected{RESET}" if connected else ""
                    print(f"  {i}) {dot} {name}  {DIM}{mac}{RESET}{status}")
                print(f"\n{DIM}  /bt <number>   connect a device")
                print(f"  /bt off        disconnect all{RESET}\n")
            elif parts[1].lower() == "off":
                for mac, name, connected in devices:
                    if connected:
                        bt_disconnect(mac)
                        print(f"{DIM}Disconnected {name}.{RESET}")
                print()
            elif parts[1].isdigit():
                idx = int(parts[1]) - 1
                if 0 <= idx < len(devices):
                    mac, name, _ = devices[idx]
                    print(f"{DIM}Connecting to {name}...{RESET}", end=" ", flush=True)
                    ok = bt_connect(mac)
                    print(f"{DIM}{'done' if ok else 'failed'}.{RESET}\n")
                else:
                    print(f"{DIM}No device #{parts[1]}.{RESET}\n")
            else:
                print(f"{DIM}Usage: /bt  /bt <number>  /bt off{RESET}\n")
            continue

        batt_level, batt_charging = get_battery()
        if batt_level is not None and batt_level < 20 and not batt_charging:
            print(f"\033[33m⚠ Battery low: {batt_level:.0f}%{RESET}", flush=True)

        music_query = extract_music_query(user_input)
        if music_query:
            _, err = youtube_play(music_query)
            if err:
                print(f"{DIM}[music] {err}{RESET}\n")
            continue

        model_id = locked_model if locked_model else route_model(user_input)
        if locked_model is None:
            print(f"{DIM}[auto → {model_label(model_id)}]{RESET}", flush=True)

        session.append({"role": "user", "content": user_input})
        append_message("user", user_input)

        reply = stream_reply(session, model_id)
        if reply:
            session.append({"role": "assistant", "content": reply})
            append_message("assistant", reply)
            if tts_on:
                tts_lang = "he" if needs_torah(user_input) else None
                speak_terminal(reply, lang=tts_lang)

        if len(session) > 60:
            session = session[-60:]

    shutdown()


if __name__ == "__main__":
    if "--https" in sys.argv or "-https" in sys.argv:
        run_web_server(port=5443, use_https=True)
    elif "--web" in sys.argv or "-web" in sys.argv:
        run_web_server()
    elif "--device" in sys.argv or "-device" in sys.argv:
        run_device_mode()
    else:
        main()
