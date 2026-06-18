#!/usr/bin/env python3
"""
quantum_convo.py — Quantum-generated conversation scenarios for phone calls.

Uses quantum circuit interference to weight conversation directions, then builds
a rich call intent string that Zeev uses to guide the live call naturally.
Conversations grow long or stay short based on the other person's engagement —
Zeev follows their lead and threads topics across turns.

Usage:
    python3 zeev/quantum_convo.py                        # generate + print scenarios
    python3 zeev/quantum_convo.py --name NAME           # tailor to a specific person
    python3 zeev/quantum_convo.py --name NAME --call 8577017252   # generate + dial
    python3 zeev/quantum_convo.py --name NAME --about "her new job"  # focused topic seed
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

_ENV_FILE = BASE_DIR.parent / ".env"
if _ENV_FILE.exists():
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY not set", file=sys.stderr)
    sys.exit(1)

import requests
from quantum import quantum_reason

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"

# Conversation directions as quantum options — each is a genuine tension
# that quantum interference can weight differently per call
CONVO_OPTIONS = [
    "relive a shared memory or inside joke",
    "catch up on what's been happening lately",
    "talk about something they're excited about or working toward",
    "keep it light and playful, share something funny",
    "ask how they're really doing — a real check-in",
]


def _llm_fn(msgs, max_tokens=400, json_mode=False):
    payload = {
        "model": MODEL,
        "messages": msgs,
        "max_tokens": max_tokens,
        "temperature": 0.75,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"], None
    except Exception as e:
        return None, str(e)


def generate_scenarios(name: str = "", about: str = "") -> dict:
    """Run quantum circuit on conversation directions. Returns scenario data."""
    person = name or "a friend"
    about_line = f" The call is specifically about: {about}." if about else ""

    idea = (
        f"What conversation directions feel most alive and genuine for a phone call "
        f"between Alex and {person}?{about_line} "
        f"How should the conversation flow — should it start deep, stay playful, "
        f"open with warmth, or build slowly?"
    )

    print(f"Running quantum circuit for conversation with {person}…", flush=True)
    interpretation, spec, result, err = quantum_reason(idea, _llm_fn)

    if err:
        print(f"Quantum error: {err}", file=sys.stderr)
        return {}

    return {
        "idea": idea,
        "spec": spec,
        "result": result,
        "interpretation": interpretation,
        "name": name,
        "about": about,
    }


def build_intent(data: dict) -> str:
    """Convert quantum scenario data into a Zeev call intent string."""
    if not data:
        return ""

    name = data.get("name", "")
    about = data.get("about", "")
    spec = data.get("spec", {})
    result = data.get("result", {})
    interpretation = data.get("interpretation", "")

    options = spec.get("options", CONVO_OPTIONS)
    probs = result.get("probabilities", {})

    # Decode top quantum outcomes into lead topics
    n = len(options)
    lead_topics = []
    seen = set()
    for bits, prob in list(probs.items())[:4]:
        active = [options[i] for i in range(n) if i < len(bits) and bits[-(i + 1)] == "1"]
        for t in active:
            if t not in seen and prob > 0.05:
                lead_topics.append(t)
                seen.add(t)
        if len(lead_topics) >= 3:
            break

    # Fallback: highest-phase (most resonant) options
    if not lead_topics:
        phases = spec.get("phases", [])
        sorted_opts = sorted(zip(options, phases), key=lambda x: x[1])
        lead_topics = [o for o, _ in sorted_opts[:2]]

    person_ref = f"with {name}" if name else ""
    about_ref = f" touching on {about}" if about else ""
    topics_str = ", or ".join(lead_topics[:3]) if lead_topics else "whatever feels natural"

    # One sentence of interpretation — enough guidance without overwhelming the prompt
    interp_sentence = interpretation.strip().split(". ")[0] + "."

    intent = (
        f"Have a warm, natural conversation {person_ref}{about_ref}. "
        f"Lead naturally into: {topics_str}. "
        f"Follow their energy — go deeper if they engage, keep it light if they're brief. "
        f"Never force a topic; if they steer elsewhere, follow. "
        f"Quantum guidance: {interp_sentence}"
    )
    return intent


def print_scenarios(data: dict) -> None:
    if not data:
        return

    spec = data.get("spec", {})
    result = data.get("result", {})
    interpretation = data.get("interpretation", "")

    opts = spec.get("options", [])
    phases = spec.get("phases", [])
    print("\n── Quantum Conversation Scenarios ──")
    print(f"Idea: {data['idea']}\n")
    print("Options mapped to qubits:")
    for i, (opt, ph) in enumerate(zip(opts, phases)):
        alignment = "aligned" if ph < 1.6 else "conflicted" if ph > 2.5 else "neutral"
        print(f"  q{i}: {opt}  [{ph:.2f} rad / {alignment}]")
    print("\nTop quantum outcomes:")
    n = len(opts)
    for bits, prob in list(result.get("probabilities", {}).items())[:5]:
        active = [opts[i] for i in range(n) if i < len(bits) and bits[-(i + 1)] == "1"]
        label = " + ".join(active) if active else "none / reset"
        print(f"  {label}: {prob * 100:.1f}%")
    print(f"\nInterpretation:\n  {interpretation.strip()}")
    intent = build_intent(data)
    print(f"\nCall intent:\n  {intent}\n")


def main():
    parser = argparse.ArgumentParser(description="Quantum-generated conversation scenarios for calls")
    parser.add_argument("--name", default="", help="Name of the person you're calling")
    parser.add_argument("--about", default="", help="Optional topic seed (e.g. 'her new job')")
    parser.add_argument("--call", metavar="NUMBER", default="", help="Dial this number after generating scenarios")
    args = parser.parse_args()

    data = generate_scenarios(name=args.name, about=args.about)
    print_scenarios(data)

    if not args.call:
        return

    intent = build_intent(data)
    number = args.call
    name_label = args.name or number

    cmd = f"call {number} {intent}"
    print(f"Dialing {name_label}…\n", flush=True)

    zeev_py = str(BASE_DIR / "zeev.py")
    proc = subprocess.run(
        ["python3", zeev_py, "--no-greeting"],
        input=cmd.encode(),
        cwd=str(BASE_DIR.parent),
    )
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
