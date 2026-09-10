---
name: skits
description: Play a short scripted comedic voice skit through the Pi's speaker using Zeev's TTS voices — real personas (Zeev, Sarina, the Russian call-mode persona Irina) plus any local Piper voice borrowed as a one-off secondary character. Use when asked for a skit, voice bit, comedic character dialogue/banter, or "have X and Y talk" played live on the device.
---

# Skits — scripted voice bits on the Pi

Writes a short back-and-forth script, alternating TTS voices, and plays it
live through the Pi's (`ragnar@ragnarok`) physical speaker. This is a
demo/entertainment tool, not part of the `zeev.py` app — nothing here goes
through `./deploy.sh`, and no real phone call is placed even when a
call-mode voice like Irina is used.

## When to use this

The user asks for a skit, a bit, banter between two voices, "make X say Y
then have Z respond," or wants to hear a scenario played out on the device
rather than just described.

## Voices available

**Daemon-routed** (`play_skit.py`'s `speak_daemon`, via the zeev-audio Unix
socket — the real production TTS path):
- `daniel` / `zeev` — Zeev's own voice (Groq Orpheus)
- `sarina` — Sarina, Zeev's partner (Kokoro `af_heart`)
- `irina` — the Russian outbound-call persona (`ru_RU-irina` via bosgame
  remote Piper). Defined only inside `run_call_mode()` in `zeev/zeev.py`
  (~line 14774) as an identity the LLM adopts for Russian phone calls —
  she isn't a standing character with her own system prompt the way
  Zeev/Sarina are, but the voice model is real and daemon-routed the same
  way.

**Local Piper** (`speak_local`, `~/piper/*.onnx` on the Pi, played directly
via `aplay` — no daemon involved), for improvised secondary/incidental
voices that aren't canonical Zeev-universe characters:
- `dmitri` (`ru_RU-dmitri-medium`, male Russian) — used as "Sasha," the
  human Irina addresses on calls, in the reference skit below
- `amy`, `ryan` (English), `jenny` (English), `libritts` (English)
- `daniela`, `ald` (Spanish)

Check `~/piper/*.onnx` on the Pi if you need a voice not listed — add it to
`LOCAL_VOICES` in `play_skit.py`.

## Writing a skit

Keep lines short (a sentence or two each — this is TTS, not a script
reading) and alternate speakers for a natural back-and-forth. A running gag
or a small comedic turn (a mistake, a misunderstanding, a reveal) works
better than a long scene. Write dialogue in whatever language fits the
voice/scenario — Piper will speak whatever text it's given in the target
language's phonemizer; feeding a voice text in a *different* language (e.g.
Ukrainian into a Russian model) still produces audio, just accented/off —
useful deliberately for a "glitching" bit, not something to do by accident.

## Running it

1. Write the script as a JSON array of `[voice, text]` pairs to a local
   file, e.g. `/tmp/lines.json`:
   ```json
   [["irina", "Здравствуйте, это Ирина, секретарь Саши."],
    ["dmitri", "Да, слушаю. Какой счёт?"]]
   ```
2. Copy the player and the script to the Pi (the player is small and
   idempotent to re-copy every time — don't assume a prior copy is current):
   ```bash
   scp .claude/skills/skits/play_skit.py /tmp/lines.json ragnar@ragnarok:/tmp/
   ```
3. Run it there — it plays synchronously, line by line:
   ```bash
   ssh ragnar@ragnarok "python3 /tmp/play_skit.py /tmp/lines.json"
   ```

## Reference skit (verified live)

Irina and Sasha start a routine call about a bill; Irina's "language pack"
glitches mid-sentence into Ukrainian; Sasha is lost; she catches herself.
```json
[["irina", "Здравствуйте! Это Ирина, секретарь Саши. У меня есть быстрый вопрос об оплате счёта."],
 ["dmitri", "Да, слушаю. Какой счёт?"],
 ["irina", "Ой, тобто... рахунок за електрику, він прострочений на два дні."],
 ["dmitri", "Простите... что? Вы сейчас на украинском заговорили?"],
 ["irina", "Так, звичайно! Хіба не зрозуміло?"],
 ["dmitri", "Нет, совсем не понятно! Ирина, вы в порядке?"],
 ["irina", "Ой... извините, кажется, я перепутала языковой пакет. Так о чём я говорила?"],
 ["dmitri", "Забудьте про счёт. Мне уже весело."]]
```

## Notes

- `play_skit.py` connects to the daemon (`AudioClient`, same socket
  `zeev.py` uses) for `daniel`/`zeev`/`sarina`/`irina`; check
  `systemctl is-active zeev-audio` on the Pi first if those lines don't
  play — local-voice lines (`dmitri`, `amy`, etc.) don't need the daemon at
  all and will still work if it's down.
- Each daemon line goes through `speak_sync`, which blocks until playback
  finishes (up to a 180s timeout) — the whole skit runs sequentially, no
  overlap.
- This reuses the exact voice IDs the real app uses (`sarina`, `daniel`,
  `ru_RU-irina`) so a skit sounds identical to how these personas actually
  speak in production — only the local secondary voices (`dmitri` etc.)
  are a deliberate departure, cast just for the bit.
