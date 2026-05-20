#!/usr/bin/env python3
"""Zeev — AI Companion"""

import base64
import io
import json
import os
import re
import readline
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
HISTORY_FILE = BASE_DIR / "data" / "history.jsonl"
MEMORY_FILE  = BASE_DIR / "data" / "user_memory.json"
RL_HISTORY   = BASE_DIR / "data" / ".readline_history"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "gsk_your_key_here")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TTS_URL = "https://api.groq.com/openai/v1/audio/speech"
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_URL     = "https://api.tavily.com/search"

# Learning state — populated by init_learning() at startup
USER_FACTS       = []   # persistent facts about the user
_HISTORY_ENTRIES = []   # raw parsed entries from history.jsonl for RAG
_HISTORY_INDEX   = {}   # word → [entry indices] inverted index

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

# Model auto-routing heuristics
_REASONING_RE = re.compile(
    r"\b(prove|proof|calculate|solve|equation|formula|math|"
    r"step.by.step|walk me through|think through|deduce|infer|"
    r"probability|logic|algorithm|complexity|optimize|"
    r"puzzle|riddle|paradox|theorem)\b",
    re.IGNORECASE,
)
_SMART_RE = re.compile(
    r"\b(write|implement|code|function|class|debug|refactor|"
    r"script|program|python|javascript|typescript|rust|golang|java|sql|"
    r"explain|summarize|compare|analyze|analyse|essay|report|draft|"
    r"architecture|design|difference between|how does|regex)\b",
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

CAMERA_AVAILABLE = False   # set by init_camera()


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


def get_battery():
    """Return (level: float, charging: bool) or (None, None) if unavailable."""
    level_resp  = _pisugar_query("get battery")
    charge_resp = _pisugar_query("get battery_charging")
    try:
        level = float(level_resp.split(": ")[1])
    except (AttributeError, IndexError, ValueError):
        level = None
    try:
        charging = charge_resp.split(": ")[1].strip().lower() == "true"
    except (AttributeError, IndexError):
        charging = None
    return level, charging


# ---------------------------------------------------------------------------
# Bluetooth device management + terminal TTS
# ---------------------------------------------------------------------------

TTS_AVAILABLE = False   # set by init_tts()
PIPER_BIN     = ""
PIPER_MODELS  = {}      # lang -> model path, populated by init_tts()

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


def _clean_for_tts(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"[*_`#~]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _piper_speak(clean, model):
    """Run Piper in a background thread, piping output to aplay."""
    def _run():
        try:
            piper_proc = subprocess.Popen(
                [PIPER_BIN, "--model", model, "--output_raw"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            aplay_proc = subprocess.Popen(
                ["aplay", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"],
                stdin=piper_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            piper_proc.stdout.close()
            piper_proc.stdin.write(clean.encode())
            piper_proc.stdin.close()
            aplay_proc.wait()
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


def speak_terminal(text):
    """Speak via Piper (en/es) or espeak-ng (he/fallback) in background."""
    if not TTS_AVAILABLE:
        return
    clean = _clean_for_tts(text)
    if not clean:
        return
    lang = detect_lang(clean)

    if PIPER_BIN and PIPER_MODELS.get(lang):
        _piper_speak(clean, PIPER_MODELS[lang])
    elif PIPER_BIN and PIPER_MODELS.get("en") and lang != "he":
        # no lang-specific Piper model, fall back to English Piper
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
    clean = _clean_for_tts(text)
    if not clean or detect_lang(clean) != "en":
        return None
    try:
        resp = requests.post(
            GROQ_TTS_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "canopylabs/orpheus-v1-english",
                "input": clean[:4096],
                "voice": "daniel",
                "response_format": "wav",
            },
            timeout=30,
        )
        return resp.content if resp.status_code == 200 else None
    except Exception:
        return None


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
    """Ask Groq to extract new user facts from session_msgs. Updates USER_FACTS in-place."""
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
    try:
        resp = requests.post(
            GROQ_URL,
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {"role": "system", "content": "You are a data extractor. Output only valid JSON."},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 300,
                "stream": False,
                "response_format": {"type": "json_object"},
            },
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            timeout=30,
        )
        if resp.status_code == 429:
            return None  # rate-limited; caller shows a warning
        resp.raise_for_status()
        new_facts = resp.json()["choices"][0]["message"]["content"]
        new_facts = json.loads(new_facts).get("facts", [])
        if isinstance(new_facts, list):
            merged = list(USER_FACTS)
            for f in new_facts:
                if isinstance(f, str) and f.strip() and f.strip() not in merged:
                    merged.append(f.strip())
            USER_FACTS = merged
            save_memory(USER_FACTS)
    except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError, ValueError):
        pass
    return USER_FACTS


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


def init_learning():
    """Load memory facts and build RAG index. Call once at startup."""
    global USER_FACTS
    USER_FACTS = load_memory()
    build_rag_index()


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

def _groq_post(msgs, model, stream=True):
    """POST to Groq. Returns (response, error_str). Retries up to 3x on network errors."""
    last_err = ""
    for attempt in range(3):
        try:
            return requests.post(
                GROQ_URL,
                json={"model": model, "messages": msgs,
                      "temperature": 0.75, "max_tokens": 400, "stream": stream},
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                stream=stream,
                timeout=60,
            ), None
        except requests.RequestException as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(1)
    return None, last_err


def _build_system_prompt(user_text, on_search=None):
    """Assemble system prompt: base + memory facts + RAG hits + optional web search."""
    parts = [SYSTEM_PROMPT]

    if USER_FACTS:
        facts_str = "\n".join(f"- {f}" for f in USER_FACTS[-20:])
        parts.append(f"\n\n## What I know about Ragnar:\n{facts_str}")

    hits = retrieve_relevant(user_text)
    if hits:
        rag_lines = []
        for u, a in hits:
            rag_lines.append(f"User: {u[:300]}\nZeev: {a[:300]}")
        parts.append("\n\n## Relevant past exchanges:\n" + "\n---\n".join(rag_lines))

    if needs_search(user_text) and TAVILY_API_KEY:
        if on_search:
            on_search(user_text)
        results = tavily_search(user_text)
        parts.append(f"\n\n[Web search results for '{user_text}']\n{results}")

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
        return base64.b64encode(buf.read()).decode()
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


def stream_reply(messages, model):
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY environment variable is not set.")

    user_text = messages[-1]["content"] if messages else ""

    def on_search(q):
        print(f"{DIM}[searching: {q}]{RESET}", flush=True)

    sys_prompt = _build_system_prompt(user_text, on_search)
    payload_msgs = [{"role": "system", "content": sys_prompt}] + messages

    resp, err = _groq_post(payload_msgs, model)
    if err:
        print(f"\nConnection error: {err}")
        return ""

    print(f"\n{CYAN}{BOLD}Zeev:{RESET} ", end="", flush=True)
    full = ""
    for line in resp.iter_lines():
        if not line or not line.startswith(b"data: "):
            continue
        data = line[6:]
        if data == b"[DONE]":
            break
        try:
            delta = json.loads(data)["choices"][0]["delta"].get("content", "")
            print(delta, end="", flush=True)
            full += delta
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
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
#snapBtn { background: #1e1e1e; color: #e8e8e8; border: 1px solid #333; }
#snapBtn:disabled { opacity: 0.4; cursor: not-allowed; }
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

<header>
  <span class="brand">Zeev</span>
  <div class="controls">
    <span id="battPct" style="display:none"></span>
    <button class="icon-btn" id="memBtn" title="Memory">&#129504;</button>
    <button class="icon-btn" id="ttsBtn" title="Toggle speech">&#128266;</button>
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
  <button class="btn" id="snapBtn" title="Take photo">&#128247;</button>
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
const clearBtn = document.getElementById("clearBtn");
const modelSel = document.getElementById("modelSel");
const memBtn = document.getElementById("memBtn");
const memOverlay = document.getElementById("memOverlay");
const memList = document.getElementById("memList");
const memorizeBtn = document.getElementById("memorizeBtn");
const memCloseBtn = document.getElementById("memCloseBtn");

fetch("/camera").then(r=>r.json()).then(d=>{if(!d.available)snapBtn.style.display="none";}).catch(()=>{snapBtn.style.display="none";});

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

// --- Speech recognition ---
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
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

    init_learning()
    init_camera()
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

            if self.path == "/tts":
                length = int(self.headers.get("Content-Length", 0))
                try:
                    data = json.loads(self.rfile.read(length))
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return
                text = data.get("text", "")
                lang = detect_lang(_clean_for_tts(text))
                audio = groq_tts(text)
                if audio:
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(audio)))
                    self.end_headers()
                    self.wfile.write(audio)
                else:
                    # Tell the browser the detected language so it can use speechSynthesis
                    body = json.dumps({"lang": lang}).encode()
                    self.send_response(503)
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
            full_reply = ""
            try:
                resp, err = _groq_post(payload_msgs, model)
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
        print(f"\n{DIM}Shutting down.{RESET}\n")


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


def main():
    init_learning()

    def shutdown(sig=None, frame=None):
        try:
            readline.write_history_file(str(RL_HISTORY))
        except Exception:
            pass
        print(f"\n{DIM}Goodbye.{RESET}\n")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        readline.read_history_file(str(RL_HISTORY))
    except FileNotFoundError:
        pass
    readline.set_history_length(200)

    init_tts()
    init_camera()

    print(f"\n{BOLD}Zeev v2.0{RESET} — AI Companion")
    print(f"{DIM}  /clear        wipe session context")
    print(f"  /forget       delete all history")
    print(f"  /model        switch model")
    print(f"  /memory       show stored facts")
    print(f"  /memorize     learn from this session")
    print(f"  /forget-fact  remove a fact by number")
    print(f"  /tts          toggle speech output")
    print(f"  /bt           manage Bluetooth devices")
    print(f"  /look [q]     take a photo and ask Zeev about it")
    print(f"  quit          exit{RESET}\n")

    locked_model = None   # None = auto-routing
    tts_on       = False
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
    print(f"{DIM}Model: auto-routing  (/model to change){RESET}\n")

    while True:
        try:
            user_input = input(f"{BOLD}You:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            break

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

        if user_input.lower() == "/tts":
            if not TTS_AVAILABLE:
                print(f"{DIM}No TTS engine found. Install Piper (recommended) or espeak-ng.")
                print(f"  Piper: download from github.com/rhasspy/piper and set PIPER_MODEL=/path/to/model.onnx")
                print(f"  espeak-ng: sudo apt install espeak-ng{RESET}\n")
                continue
            tts_on = not tts_on
            state = "on" if tts_on else "off"
            print(f"{DIM}Speech {state}.{RESET}\n")
            continue

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
                speak_terminal(reply)

        if len(session) > 60:
            session = session[-60:]

    shutdown()


if __name__ == "__main__":
    if "--https" in sys.argv or "-https" in sys.argv:
        run_web_server(port=5443, use_https=True)
    elif "--web" in sys.argv or "-web" in sys.argv:
        run_web_server()
    else:
        main()
