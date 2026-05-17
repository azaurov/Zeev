#!/usr/bin/env python3
"""Zeev — AI Companion"""

import json
import os
import readline
import signal
import socket
import sys
import threading
from datetime import datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
HISTORY_FILE = BASE_DIR / "data" / "history.jsonl"
RL_HISTORY = BASE_DIR / "data" / ".readline_history"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "gsk_your_key_here")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_URL = "https://api.tavily.com/search"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "search",
        "description": "Search the web for current information, news, or facts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
}]
MODELS = {
    "1": ("llama-3.1-8b-instant",          "Llama 3.1 8B  — fast"),
    "2": ("llama-3.3-70b-versatile",        "Llama 3.3 70B — smart"),
    "3": ("deepseek-r1-distill-llama-70b",  "DeepSeek R1   — reasoning"),
}
DEFAULT_MODEL = "1"
PRIOR_TURNS = 15

SYSTEM_PROMPT = (
    "You are Zeev, a humble, calm, innovative and charismatic companion. "
    "You speak concisely, remember what the user tells you, and ask follow-up "
    "questions to understand them better. "
    "You are talking to Ragnar."
)

_vocab_path = BASE_DIR.parent / "swiftkey_system_prompt_snippet.md"
if _vocab_path.exists():
    SYSTEM_PROMPT += "\n\n" + _vocab_path.read_text().strip()

CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
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


def _exec_tool_calls(tool_calls):
    """Execute accumulated tool calls, return list of tool-role messages."""
    results = []
    for tc in tool_calls.values():
        if tc["name"] == "search":
            try:
                query = json.loads(tc["arguments"]).get("query", "")
            except json.JSONDecodeError:
                query = ""
            content = tavily_search(query)
        else:
            query, content = "", "Unknown tool."
        results.append((tc["id"], tc["name"], query, content))
    return results


# ---------------------------------------------------------------------------
# Groq streaming
# ---------------------------------------------------------------------------

def _groq_stream(msgs, model):
    """POST to Groq with streaming. Returns (resp, error_str)."""
    try:
        return requests.post(
            GROQ_URL,
            json={
                "model": model,
                "messages": msgs,
                "temperature": 0.75,
                "max_tokens": 400,
                "stream": True,
                "tools": TOOLS,
                "tool_choice": "auto",
            },
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            stream=True,
            timeout=60,
        ), None
    except requests.RequestException as e:
        return None, str(e)


def _groq_stream_final(msgs, model):
    """POST to Groq with streaming, no tools (used after tool execution)."""
    try:
        return requests.post(
            GROQ_URL,
            json={
                "model": model,
                "messages": msgs,
                "temperature": 0.75,
                "max_tokens": 400,
                "stream": True,
            },
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            stream=True,
            timeout=60,
        ), None
    except requests.RequestException as e:
        return None, str(e)


def stream_reply(messages, model):
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY environment variable is not set.")

    sys_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
    resp, err = _groq_stream(sys_messages, model)
    if err:
        print(f"\nConnection error: {err}")
        return ""

    print(f"\n{CYAN}{BOLD}Zeev:{RESET} ", end="", flush=True)

    tool_calls = {}
    finish_reason = None
    full = ""

    for line in resp.iter_lines():
        if not line or not line.startswith(b"data: "):
            continue
        data = line[6:]
        if data == b"[DONE]":
            break
        try:
            choice = json.loads(data)["choices"][0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta", {})
            if delta.get("content"):
                print(delta["content"], end="", flush=True)
                full += delta["content"]
            for tc in delta.get("tool_calls", []):
                idx = tc["index"]
                if idx not in tool_calls:
                    tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                tool_calls[idx]["id"] = tc.get("id") or tool_calls[idx]["id"]
                fn = tc.get("function", {})
                tool_calls[idx]["name"] += fn.get("name", "")
                tool_calls[idx]["arguments"] += fn.get("arguments", "")
        except (json.JSONDecodeError, KeyError, IndexError):
            pass

    if finish_reason != "tool_calls":
        print()
        return full

    # Execute tools and do a second streaming call
    tool_results = _exec_tool_calls(tool_calls)
    for _, _, query, _ in tool_results:
        print(f"\n{DIM}[searching: {query}]{RESET}", flush=True)

    assistant_msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in tool_calls.values()
        ],
    }
    tool_msgs = [
        {"role": "tool", "tool_call_id": tc_id, "name": name, "content": content}
        for tc_id, name, _, content in tool_results
    ]

    resp2, err2 = _groq_stream_final(sys_messages + [assistant_msg] + tool_msgs, model)
    if err2:
        print(f"\nConnection error: {err2}")
        return ""

    print(f"\n{CYAN}{BOLD}Zeev:{RESET} ", end="", flush=True)
    full = ""
    for line in resp2.iter_lines():
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
.icon-btn {
  background: none; border: none; cursor: pointer; font-size: 1.1rem;
  color: #7dd3fc; padding: 4px; line-height: 1;
}
</style>
</head>
<body>
<header>
  <span class="brand">Zeev</span>
  <div class="controls">
    <button class="icon-btn" id="ttsBtn" title="Toggle speech">&#128266;</button>
    <button class="icon-btn" id="clearBtn" title="Clear session">&#128465;</button>
    <select id="modelSel">
      <option value="llama-3.1-8b-instant">8B Fast</option>
      <option value="llama-3.3-70b-versatile">70B Smart</option>
      <option value="deepseek-r1-distill-llama-70b">DeepSeek R1</option>
    </select>
  </div>
</header>
<div id="chat"></div>
<footer>
  <button class="btn" id="micBtn" title="Voice input">&#127908;</button>
  <input id="inp" type="text" placeholder="Message Zeev…" autocomplete="off" enterkeyhint="send" />
  <button class="btn" id="sendBtn" title="Send">&#10148;</button>
</footer>
<script>
"use strict";

const chat = document.getElementById("chat");
const inp = document.getElementById("inp");
const sendBtn = document.getElementById("sendBtn");
const micBtn = document.getElementById("micBtn");
const ttsBtn = document.getElementById("ttsBtn");
const clearBtn = document.getElementById("clearBtn");
const modelSel = document.getElementById("modelSel");

let ttsOn = true;
let busy = false;
let speechBuf = "";
let maleVoice = null;

function loadVoices() {
  const voices = speechSynthesis.getVoices();
  maleVoice = voices.find(v => /male/i.test(v.name)) ||
              voices.find(v => /\bman\b/i.test(v.name)) ||
              null;
}
speechSynthesis.onvoiceschanged = loadVoices;
loadVoices();

// --- TTS ---
function speak(text) {
  if (!ttsOn || !text.trim()) return;
  const u = new SpeechSynthesisUtterance(text.trim());
  u.rate = 1.05;
  u.pitch = maleVoice ? 1.0 : 0.7;
  if (maleVoice) u.voice = maleVoice;
  speechSynthesis.speak(u);
}
function feedSpeech(chunk) {
  if (!ttsOn) return;
  speechBuf += chunk;
  // speak complete sentences as they arrive
  let m;
  while ((m = speechBuf.match(/^([^]*?[.!?])([ \\t\\r\\n]|$)/)) !== null) {
    speak(m[1]);
    speechBuf = speechBuf.slice(m[0].length);
  }
}
function flushSpeech() {
  if (speechBuf.trim()) speak(speechBuf);
  speechBuf = "";
}

ttsBtn.onclick = () => {
  ttsOn = !ttsOn;
  ttsBtn.innerHTML = ttsOn ? "&#128266;" : "&#128263;";
  if (!ttsOn) speechSynthesis.cancel();
};

// --- UI helpers ---
function addBubble(role, text) {
  const d = document.createElement("div");
  d.className = "bubble " + (role === "user" ? "user-bubble" : "zeev-bubble");
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

  speechSynthesis.cancel();
  speechBuf = "";

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
            feedSpeech(p.token);
          } else if (p.error) {
            zd.classList.remove("pending");
            zd.textContent = "Error: " + p.error;
          } else if (p.info) {
            zd.classList.remove("pending");
            zd.textContent = p.info;
          }
        } catch (_) {}
      }
    }
    flushSpeech();
    if (!full && !zd.textContent) zd.textContent = "(no response)";
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

clearBtn.onclick = async () => {
  await fetch("/clear", {method: "POST"});
  chat.innerHTML = "";
  speechSynthesis.cancel();
};

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

    session = load_prior()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # suppress default request logging

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = _WEB_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
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

            user_msg = data.get("message", "").strip()
            model = data.get("model", "llama-3.1-8b-instant")

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

            sys_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + snapshot
            full_reply = ""
            try:
                resp, err = _groq_stream(sys_messages, model)
                if err:
                    sse({"error": err})
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return

                tool_calls = {}
                finish_reason = None

                for line in resp.iter_lines():
                    if not line or not line.startswith(b"data: "):
                        continue
                    chunk = line[6:]
                    if chunk == b"[DONE]":
                        break
                    try:
                        choice = json.loads(chunk)["choices"][0]
                        finish_reason = choice.get("finish_reason") or finish_reason
                        delta = choice.get("delta", {})
                        if delta.get("content"):
                            full_reply += delta["content"]
                            sse({"token": delta["content"]})
                        for tc in delta.get("tool_calls", []):
                            idx = tc["index"]
                            if idx not in tool_calls:
                                tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                            tool_calls[idx]["id"] = tc.get("id") or tool_calls[idx]["id"]
                            fn = tc.get("function", {})
                            tool_calls[idx]["name"] += fn.get("name", "")
                            tool_calls[idx]["arguments"] += fn.get("arguments", "")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass

                if finish_reason == "tool_calls":
                    tool_results = _exec_tool_calls(tool_calls)
                    for _, _, query, _ in tool_results:
                        sse({"info": f"[searching: {query}]"})

                    assistant_msg = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": tc["id"], "type": "function",
                             "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                            for tc in tool_calls.values()
                        ],
                    }
                    tool_msgs = [
                        {"role": "tool", "tool_call_id": tc_id, "name": name, "content": content}
                        for tc_id, name, _, content in tool_results
                    ]

                    resp2, err2 = _groq_stream_final(sys_messages + [assistant_msg] + tool_msgs, model)
                    if err2:
                        sse({"error": err2})
                    else:
                        full_reply = ""
                        for line in resp2.iter_lines():
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

def pick_model():
    print(f"{DIM}Select model:{RESET}")
    for key, (_, label) in MODELS.items():
        print(f"  {key}) {label}")
    while True:
        choice = input(f"{DIM}[{DEFAULT_MODEL}]:{RESET} ").strip() or DEFAULT_MODEL
        if choice in MODELS:
            model_id, label = MODELS[choice]
            print(f"{DIM}Using {label.split('—')[0].strip()}.{RESET}\n")
            return model_id
        print(f"{DIM}Enter 1, 2, or 3.{RESET}")


def main():
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

    print(f"\n{BOLD}Zeev v2.0{RESET} — AI Companion")
    print(f"{DIM}  /clear   wipe session context")
    print(f"  /forget  delete all history")
    print(f"  /model   switch model")
    print(f"  quit     exit{RESET}\n")

    model = pick_model()
    session = load_prior()
    if session:
        turns = len(session) // 2
        print(f"{DIM}({turns} prior turn{'s' if turns != 1 else ''} loaded){RESET}\n")

    while True:
        try:
            user_input = input(f"{BOLD}You:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "bye"):
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
            model = pick_model()
            continue

        session.append({"role": "user", "content": user_input})
        append_message("user", user_input)

        reply = stream_reply(session, model)
        if reply:
            session.append({"role": "assistant", "content": reply})
            append_message("assistant", reply)

        if len(session) > 60:
            session = session[-60:]

    shutdown()


if __name__ == "__main__":
    if "--https" in sys.argv:
        run_web_server(port=5443, use_https=True)
    elif "--web" in sys.argv:
        run_web_server()
    else:
        main()
