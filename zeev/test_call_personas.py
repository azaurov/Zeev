#!/usr/bin/env python3
"""
test_call_personas.py — persona + emotion system tests for bt_call_loop

Tests two modes:
  1. TTS synthesis only (--tts-only): generates a sample phrase for each persona,
     saves WAV to /tmp/zeev_persona_<name>.wav, no phone call made.
  2. Live call (default): dials TEST_NUMBER and leaves a voicemail for each scenario.

Usage:
  python3 zeev/test_call_personas.py --tts-only          # synthesise only
  python3 zeev/test_call_personas.py                     # live calls (slow, uses minutes)
  python3 zeev/test_call_personas.py --persona friendly  # single persona TTS test
"""

import argparse
import os
import subprocess
import sys
import time

# Ensure zeev package is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Scenarios — each maps to a call intent string that extract_call_persona()
# will classify, plus an expected persona for assertion.
# ---------------------------------------------------------------------------
SCENARIOS: list[dict] = [
    {
        "name": "irate_boss",
        "label": "Irate Boss",
        "intent": "tell him I'm furious about the missed deadline and demand an explanation today",
        "expected_persona": "authoritative",
        "sample_phrase": "This is completely unacceptable. I need answers on that report by end of day — no excuses.",
    },
    {
        "name": "colleague",
        "label": "Colleague",
        "intent": "let her know about the meeting change, keep it professional and brief",
        "expected_persona": "professional",
        "sample_phrase": "Hi, just a quick heads-up — the 3pm meeting has moved to 4pm. See you then.",
    },
    {
        "name": "friend",
        "label": "Friend",
        "intent": "leave a friendly message saying I'll be at the party, super excited",
        "expected_persona": "friendly",
        "sample_phrase": "Hey, just wanted to say I'm definitely coming tonight — can't wait, it's going to be so fun!",
    },
    {
        "name": "parent",
        "label": "Parent",
        "intent": "call my son and leave a caring message reminding him to call back",
        "expected_persona": "nurturing",
        "sample_phrase": "Hi sweetheart, it's me. Just checking in — give me a call when you get a chance. Love you.",
    },
    {
        "name": "lover",
        "label": "Lover",
        "intent": "leave a romantic message for my darling, I miss her",
        "expected_persona": "intimate",
        "sample_phrase": "Hey you — just thinking about you and wanted to hear your voice. Miss you already.",
    },
    {
        "name": "mistress",
        "label": "Mistress",
        "intent": "leave an intimate message for my mistress, keep it discreet",
        "expected_persona": "intimate",
        "sample_phrase": "It's me. Was just thinking about last night. Can't stop smiling. Call me when you're free.",
    },
    {
        "name": "calm",
        "label": "Calm / Meditative",
        "intent": "leave a calm gentle reminder about the appointment tomorrow",
        "expected_persona": "calm",
        "sample_phrase": "Just a gentle reminder about your appointment tomorrow morning. Take care and rest well.",
    },
]

TEST_NUMBER = "6174106801"  # Google Voice voicemail


def run_tts_test(scenarios: list[dict]) -> None:
    """Synthesise one phrase per persona and save to /tmp. No calls made."""
    # Import after sys.path tweak
    from zeev import (
        cartesia_tts, extract_call_persona, _CALL_VOICES,
        CARTESIA_API_KEY,
    )

    if not CARTESIA_API_KEY:
        print("ERROR: CARTESIA_API_KEY not set — cannot run TTS tests.", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'='*60}")
    print("PERSONA TTS SYNTHESIS TEST")
    print(f"{'='*60}\n")

    passed = failed = 0

    for s in scenarios:
        intent = s["intent"]
        detected = extract_call_persona(intent)
        expected = s["expected_persona"]
        cfg = _CALL_VOICES.get(detected, {})
        cartesia_id = cfg.get("cartesia", "?")

        status = "PASS" if detected == expected else "FAIL"
        if detected == expected:
            passed += 1
        else:
            failed += 1

        gender = cfg.get("gender", "?")
        orpheus_voice = cfg.get("orpheus", "?")
        print(f"[{status}] {s['label']}  [{gender}]")
        print(f"  intent   : {intent[:70]}")
        print(f"  expected : {expected}  →  detected: {detected}  ({cfg.get('label', '?')})")
        print(f"  orpheus  : {orpheus_voice}  |  cartesia: {cartesia_id}")

        # Synthesise sample phrase
        wav = cartesia_tts(s["sample_phrase"], persona=detected)
        if wav:
            out_path = f"/tmp/zeev_persona_{s['name']}.wav"
            with open(out_path, "wb") as f:
                f.write(wav)
            print(f"  audio    : {out_path}  ({len(wav)} bytes)")
        else:
            print("  audio    : FAILED (Cartesia returned None)")
        print()

    print(f"{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*60}\n")

    if failed:
        sys.exit(1)


def run_live_call_test(scenarios: list[dict], number: str) -> None:
    """Dial number once per scenario and leave a voicemail. Slow — uses phone minutes."""
    print(f"\n{'='*60}")
    print(f"LIVE CALL PERSONA TEST → {number}")
    print("Each call leaves a voicemail. Waiting 35s between calls.")
    print(f"{'='*60}\n")

    for i, s in enumerate(scenarios):
        print(f"[{i+1}/{len(scenarios)}] {s['label']} — persona: {s['expected_persona']}")
        intent = f"{s['intent']} (persona test: {s['name']})"
        cmd = f"echo 'call {number} to {intent}' | python3 zeev/zeev.py --no-greeting"
        print(f"  $ {cmd}")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=90)
        # Print call-relevant log lines
        for line in (result.stdout + result.stderr).splitlines():
            if any(t in line for t in ("[call]", "[tts]", "[bt]", "Detected", "Zeev")):
                print(f"  {line}")
        print()
        if i < len(scenarios) - 1:
            print("  Waiting 35s before next call...")
            time.sleep(35)

    print("Live call tests complete. Check your voicemail inbox.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tts-only", action="store_true",
                        help="Synthesise TTS only — no phone calls")
    parser.add_argument("--persona", metavar="NAME",
                        help="Test a single persona by name")
    parser.add_argument("--number", default=TEST_NUMBER,
                        help=f"Phone number to call (default: {TEST_NUMBER})")
    args = parser.parse_args()

    scenarios = SCENARIOS
    if args.persona:
        scenarios = [s for s in SCENARIOS if s["name"] == args.persona
                     or s["expected_persona"] == args.persona]
        if not scenarios:
            print(f"No scenario found for --persona {args.persona!r}", file=sys.stderr)
            print(f"Available: {', '.join(s['name'] for s in SCENARIOS)}")
            sys.exit(1)

    if args.tts_only:
        run_tts_test(scenarios)
    else:
        run_live_call_test(scenarios, args.number)


if __name__ == "__main__":
    main()
