#!/usr/bin/env python3
"""Zeev v2.0 — AI Companion"""

import json
import os
import readline
import signal
import sys
from datetime import datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
HISTORY_FILE = BASE_DIR / "data" / "history.jsonl"
RL_HISTORY = BASE_DIR / "data" / ".readline_history"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODELS = {
    "1": ("llama-3.1-8b-instant",          "Llama 3.1 8B  — fast"),
    "2": ("llama-3.3-70b-versatile",        "Llama 3.3 70B — smart"),
    "3": ("deepseek-r1-distill-llama-70b",  "DeepSeek R1   — reasoning"),
}
DEFAULT_MODEL = "1"
PRIOR_TURNS = 15  # turns loaded from previous sessions

SYSTEM_PROMPT = (
    "You are Zeev, a humble, calm, innovative and charismatic companion. "
    "You speak concisely, remember what the user tells you, and ask follow-up "
    "questions to understand them better."
)

CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


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


def stream_reply(messages, model):
    if not GROQ_API_KEY:
        sys.exit("GROQ_API_KEY environment variable is not set.")
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "temperature": 0.75,
        "max_tokens": 400,
        "stream": True,
    }
    try:
        resp = requests.post(
            GROQ_URL,
            json=payload,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            stream=True,
            timeout=60,
        )
    except requests.RequestException as e:
        print(f"\nConnection error: {e}")
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

        # Keep last 60 messages in session to bound memory
        if len(session) > 60:
            session = session[-60:]

    shutdown()


if __name__ == "__main__":
    main()
