#!/usr/bin/env python3
"""
Play a short scripted voice skit through the Pi's speaker, line by line,
blocking so lines play back-to-back in order. Lives and runs on the Pi
(ragnar@ragnarok) — not part of the zeev.py app, not deployed via
./deploy.sh.

Usage (on the Pi):
    python3 play_skit.py lines.json

lines.json is a JSON array of [voice, text] pairs:
    [["irina", "Здравствуйте, это Ирина..."],
     ["dmitri", "Да, слушаю."],
     ...]

Voice names:

Daemon-routed (real Zeev personas, spoken via the zeev-audio Unix socket,
the same path production turns use):
    "daniel" / "zeev" -> Zeev's own voice
    "sarina"          -> Sarina, Zeev's partner (Kokoro af_heart)
    "irina"           -> the Russian call-mode persona (ru_RU-irina via
                         bosgame remote Piper) -- see zeev/zeev.py's
                         run_call_mode() for where "Irina" is defined; she
                         is a call-only identity, not a standing persona

Local Piper (~/piper/*.onnx), for one-off/secondary skit voices that have
no daemon route of their own -- borrowed voices for a bit part, not
canonical Zeev-universe characters:
    "dmitri"   -> ru_RU-dmitri-medium.onnx (male Russian)
    "amy"      -> en_US-amy-medium.onnx
    "ryan"     -> en_US-ryan-medium.onnx
    "jenny"    -> en_GB-jenny_dioco-medium.onnx
    "libritts" -> en_US-libritts_r-medium.onnx
    "daniela"  -> es_AR-daniela-high.onnx
    "ald"      -> es_MX-ald-medium.onnx

Add more by dropping a model into ~/piper/ and adding it to LOCAL_VOICES.
"""
import json
import subprocess
import sys

sys.path.insert(0, "/home/ragnar/Zeev/zeev")
from audio_client import AudioClient  # noqa: E402

DAEMON_VOICES = {"daniel", "zeev", "sarina"}
DAEMON_LANG = {"irina": "ru"}  # voice -> lang passed to speak_sync

LOCAL_VOICES = {
    "dmitri": "ru_RU-dmitri-medium.onnx",
    "amy": "en_US-amy-medium.onnx",
    "ryan": "en_US-ryan-medium.onnx",
    "jenny": "en_GB-jenny_dioco-medium.onnx",
    "libritts": "en_US-libritts_r-medium.onnx",
    "daniela": "es_AR-daniela-high.onnx",
    "ald": "es_MX-ald-medium.onnx",
}

PIPER_BIN = "/home/ragnar/piper/piper/piper"
PIPER_DIR = "/home/ragnar/piper"
TMP_WAV = "/tmp/skit_line.wav"


def speak_daemon(client, voice, text):
    lang = DAEMON_LANG.get(voice, "en")
    kwargs = {"lang": lang}
    if voice in ("daniel", "zeev"):
        kwargs["voice"] = "daniel"
    elif voice == "sarina":
        kwargs["voice"] = "sarina"
    ok = client.speak_sync(text, **kwargs)
    if not ok:
        print(f"  [warn] daemon speak_sync failed for voice={voice!r}", file=sys.stderr)


def speak_local(voice, text):
    model = f"{PIPER_DIR}/{LOCAL_VOICES[voice]}"
    subprocess.run(
        ["bash", "-c",
         f"{PIPER_BIN} --model {model} --output_file {TMP_WAV} 2>/dev/null && "
         f"aplay {TMP_WAV} 2>/dev/null"],
        input=text.encode("utf-8"),
    )


def main():
    if len(sys.argv) != 2:
        print("usage: play_skit.py lines.json", file=sys.stderr)
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        lines = json.load(f)

    client = AudioClient()
    for voice, text in lines:
        print(f"[{voice}] {text}")
        if voice in DAEMON_VOICES or voice in DAEMON_LANG:
            speak_daemon(client, voice, text)
        elif voice in LOCAL_VOICES:
            speak_local(voice, text)
        else:
            print(f"  [error] unknown voice {voice!r}, skipping", file=sys.stderr)


if __name__ == "__main__":
    main()
