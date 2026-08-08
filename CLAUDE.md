# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Hardware Environment

Raspberry Pi Zero 2W (512 MB RAM, 4× Cortex-A53). Hardware HATs: Whisplay display (1.96" ST7789 LCD, WM8960 audio, RGB LED, KEY on GPIO17), PiSugar battery. Always verify hardware-related config changes (`config.txt`, I2C/SPI baudrates) before assuming software causes for audio/display failures.

## zeev-audio Go daemon

`zeev-audio/` handles latency-sensitive audio: Piper TTS (persistent ONNX, warm on startup), BT detection/scan/pair/connect, WM8960 keepalive, VAD recording, music playback, SCO call-audio. Python delegates via `zeev/audio_client.py`; falls back to its own subprocess implementations when unavailable.

**Wire protocol**: NDJSON over Unix socket `/tmp/zeev-audio.sock`. One JSON line per request/response.

**Commands**:
| cmd | purpose |
|---|---|
| `speak` / `speak_sync` | Remote Kokoro TTS on bosgame (24kHz) or local Piper fallback; WAV rate parsed from header; ffmpeg resample → aplay |
| `speak_sco` | Kokoro/Piper → ffmpeg resample to SCO rate → aplay on SCO device |
| `vol_get` / `vol_set` | amixer Master → wm8960 Speaker fallback |
| `bt_detect` / `bt_verify` | bluealsa-aplay PCM query |
| `bt_scan` / `bt_pair` / `bt_connect` / `bt_disconnect` | bluetoothctl wrappers |
| `audio_dev` | active ALSA PCM string |
| `play` / `stop` | yt-dlp → ffmpeg → aplay music |
| `record` | arecord + RMS VAD, returns WAV bytes; `rate` field (default 16000) |
| `sco_record` | arecord from SCO capture at negotiated rate |
| `health` | load/mem/battery summary |

**Daemon startup**: pre-warms Piper ONNX on `Init()`. Pi Zero 2W: ~5s warm / ~20s cold. Also starts WM8960 keepalive goroutine (1s silence every 20s) and `bt.Detect(2)`.

**systemd**: `zeev-audio.service`, `User=ragnar`, `Type=notify` + `WatchdogSec=30`, `Restart=on-failure`. `internal/sdnotify` sends `READY=1` on startup; a goroutine self-dials the daemon's own socket every ~15s with a `health` request and only pings `WATCHDOG=1` if that round trip succeeds — validates the full accept→dispatch→respond path, so a livelocked (not crashed) daemon still gets restarted.

**Reliability**: `internal/bt/connect.go` `Connect`/`Disconnect`/`Pair` use `exec.CommandContext` with a 10s `bluetoothctl` timeout (previously unbounded — a wedged adapter leaked a goroutine + zombie process). `piper.go`'s `SynthesizeOneShot` similarly bounded to 60s. In `zeev.py`, ~44 previously-silent `except Exception: pass` blocks on TTS/STT/BT/DB/GPS/device-startup paths now log via `[tag] ... error: {e}` — root cause of several past regressions; ~34 sites left silent (cosmetic/expected-fail/already-logged).

**BT scan/detect bugs (found live, 2026-07-26)**: `bt.Scan()` used `timeout N bluetoothctl scan on`, but bluez 5.82's one-shot `scan on` exits in ~50ms instead of blocking, so scans always returned zero devices — fixed to bluez's own `bluetoothctl --timeout N scan on` (also fixed in both Python call sites). Separately, `parsePCMs()` checked rate/channel regexes against the device-header line instead of the codec line that follows, and `rateRe` required no space before `Hz` though real output has one — `_BT_RATE` silently defaulted to 44100 regardless of actual rate. Python's `bt_detect_connected()` already parsed this correctly; only Go had the bug.

**Building and deploying**:
```bash
cd zeev-audio && make pi
ssh ragnar@ragnarok "sudo systemctl stop zeev-audio"
scp bin/zeev-audio-pi ragnar@ragnarok:~/zeev-audio/zeev-audio
scp ../zeev/audio_client.py ragnar@ragnarok:~/Zeev/zeev/audio_client.py
ssh ragnar@ragnarok "sudo systemctl start zeev-audio"
```

## TTS / Audio

**Critical constraints**:
- Piper persistent process for wired speaker; **do NOT** use per-sentence model reloads or short inter-chunk timeouts (0.3s timeout caused only the first sentence to play — burned us before).
- `_collect_piper_audio(p, first_timeout=30.0, idle_timeout=8.0)` — 30s first-chunk timeout covers cold ONNX load (~30s cold / ~5s warm). 8s idle timeout between chunks; inter-sentence gap on Pi Zero 2W warm: ~4.5s. Previous 2s timeout truncated after sentence one; 5s had only 0.5s margin.
- **BT one-shot vs persistent**: when BT headphones are connected, `_speak_device()` uses a **one-shot** Piper subprocess (stdin close → read stdout until EOF) — guarantees full multi-sentence capture before playback. Persistent process only for wired speaker.
- **BT audio resampling**: all BlueALSA output must match negotiated A2DP format (`_BT_RATE`/`_BT_CHANNELS` from `bluealsa-aplay --list-pcms`). **Do NOT** use `plug:bluealsa:...` ALSA syntax — colons in nested params break the parser. Pass explicit `-f S16_LE -r _BT_RATE -c _BT_CHANNELS` flags to `aplay`.
- WM8960 auto-powers-down after ~30s ALSA inactivity. Daemon runs keepalive; when unavailable, device mode runs Python keepalive thread.
- When daemon is running, `_speak_device()` and `speak_terminal()` delegate Piper to `_audio.speak_sync()` immediately — no Python subprocess. Python WM8960 keepalive and Piper prewarm threads are skipped (daemon handles both).
- `_speak_device()` retries Piper once on `BrokenPipeError`/`OSError` or empty audio before falling through to espeak-ng.
- **Kokoro daemon failure falls through to Orpheus, not an espeak loop** (fixed 2026-08-07). Root cause: a long reply's sentence-chunk could come back empty text server-side, both remote Kokoro backends 400'd on it, and the Go daemon masked the failure by re-speaking the *entire* reply via espeak-ng from scratch — which on a long reply ran past the Python client's 180s `speak_sync` timeout, triggering a reconnect-and-resend of the same giant request and repeating the whole cycle (observed live as TTS looping). Fixed with a `skip_espeak` request flag (`proto.Request.SkipEspeak`) — device-mode's English voice path sets it, so a Kokoro failure returns the error to Python immediately instead of being masked, and `_speak_device()`'s existing (previously unreachable) Groq Orpheus fallback picks it up. Other callers (Russian/Spanish remote-piper path, terminal `speak_sync` calls) default `skip_espeak=false` and keep the daemon's own espeak fallback, since they have no better alternative queued. Live-verified: forced a real Kokoro outage, confirmed the daemon returned `"skip_espeak set, returning error"` in 0.0s (no retry loop) and Zeev spoke via Orpheus.
- Always declare globals (e.g. `_SETTINGS_TTS_ON`) before use to avoid `SyntaxError`s.
- **`_gtts_fetch_chunk`** uses `urllib.request` (not `requests`) to avoid shared urllib3 connection pool conflicts with the bosgame fallback stream.

## Bluetooth

- `_BT_AUDIO_DEV` — active BlueALSA device string (`bluealsa:DEV=XX:XX,PROFILE=a2dp`).
- `_BT_RATE` / `_BT_CHANNELS` — negotiated A2DP format, queried from `bluealsa-aplay --list-pcms`.
- `bt_detect_connected()` — device mode startup; sets globals; retries 2× with 1s sleep.
- `bt_verify_connected()` — called at top of every `_speak_device()` call; clears `_BT_AUDIO_DEV` if device no longer listed (handles physical disconnects).
- `bt_scan()` — 10s scan via `subprocess.run(['bluetoothctl', '--timeout', N, 'scan', 'on'])`, parses output after completion. **Must use bluez's own `--timeout` flag, not an external `timeout N` wrapper** — on bluez 5.82, one-shot `scan on` exits in ~50ms rather than blocking, so the outer wrapper had nothing to kill (found live 2026-07-26).
- Startup BT volume: raw 50/127 (~39%) via `amixer -D bluealsa cset numid=2 50`.
- `/bt` slash command: `scan`, `pair <N>`, `<N>` to connect, `off` to disconnect.

## Phone Calls (HFP)

- **SCO audio device**: `bluealsa:DEV=<mac>,PROFILE=sco,SRV=org.bluealsa` (BlueALSA v4.3.1+).
- **`bt_speak_sco` TTS chain**: Groq Orpheus → Cartesia (`sonic-2`, `CARTESIA_API_KEY`/`CARTESIA_VOICE_ID` in `.env`) → Piper (via daemon `speak_sco` or Python subprocess) → gTTS. All resampled via ffmpeg to SCO rate.
- **`bt_call_loop`**: turn 0 uses `bt_fast_detect` (records ≤6s, early-exits at ~3s on speech onset). Live-person check runs before voicemail regex: early onset + short transcript → `live` immediately — prevents Whisper hallucinations on 8kHz SCO audio from misclassifying a pickup as voicemail. `_speech_onset_ms` = ms offset of first speech-energy frame (RMS > 400); onset at 0ms = pickup click, not speech.
- **Speculative pre-generation**: background thread pre-generates the voicemail message via LLM while ringing (`_pregen_msg`), cutting post-beep latency from ~14s to ~5s.
- **Call type detection**: voicemail (regex) → leave message + hangup; IVR (menu prompt regex) → DTMF digits; live/unknown → conversation. IVR hangup detection skipped ("Goodbye" is part of menu).
- **HFP guard**: `bt_hfp_detect()` after post-dial sleep; empty → hang up instead of entering `bt_call_loop` with an invalid SCO device (previously a hard hang).
- **Whisper hallucination filter**: known ring-tone hallucinations ("Thank you.", "thanks", etc.) filtered before incrementing `turn`. `groq_stt_call` biases Whisper toward call vocabulary via `_CALL_WHISPER_PROMPT` on 8kHz audio.
- **`zeev/quantum_convo.py`** — quantum-weighted conversation topics for calls: `python3 zeev/quantum_convo.py --name NAME [--about TOPIC] [--call NUMBER]`.
- **Call-intent detection**: `_bt_call_match()` gates both call sites — requires a `dial`/`phone`/`ring` trigger within the first 60 chars. Trigger is **"dial"**, not "call" (dropped 2026-07-26, found live) — "call" is too common in casual speech, and with the old 15-char gate rejected vocative lead-ins ("Hi Serena, can you call my wife..." falls through to plain chat instead of dialing).
- **Call outcome never reached the chat LLM at all, so a follow-up question about it got fabricated** (found live 2026-08-05, via `rag_probe.py`). `bt_call_loop` runs in a background thread and logs each turn only to its own `call_transcript.txt` — it never calls `append_message()`, so nothing about whether the call connected, hit voicemail, or who talked to whom ever reached the shared `messages` table. Live sequence: user asked Zeev to call Maria; two turns later, "Did you get to make the call?" got answered with a wholly invented "Yes, I made the call. It connected, and Sarina had a pleasant conversation with your wife Maria" — then, once caught out, the model flipped to an equally wrong "I don't have the capability to physically make phone calls" (device mode/terminal genuinely can, via HFP). That fabricated exchange then sat in `messages`/`message_vecs` and kept getting served back by RAG as if it really happened, producing a *further* hallucination in the probe run days later ("I can indeed place phone calls over Bluetooth HFP, and I've done so before").
  - **Fixed with a new `call_outcomes` table** (`number`, `outcome`, `ts`) — deliberately its own table, never `messages`, same reasoning as `dreams` (a fabricated line in `messages` gets embedded and served back as fact). `bt_call_loop` now takes a `dialed_number` param (threaded from all three call sites: device mode, terminal, `run_call_mode`), tracks a short honest outcome string at each of its 6 exit points (voicemail/live conversation/hangup/no-answer), and calls `log_call_outcome()` unconditionally at the end regardless of which exit fired.
  - **Surfaced ambiently in `_build_system_prompt`**, not gated behind a keyword regex — "did it work?" is too open-ended to reliably match, the same reasoning as the ambient location/time blocks. `recent_call_outcome()` returns the latest row only if within `_CALL_OUTCOME_AMBIENT_WINDOW` (30 min), so it doesn't linger into an unrelated later conversation. Fails open (returns `None`, doesn't raise) on any DB error, since this runs on every turn and a missing/broken table must not take down the whole prompt.
  - **The poisoned exchange was deleted from the Pi's live `messages` and `message_vecs`** (both halves — a user row and its assistant row, same pairing rule the vision-hallucination cleanups already established) after backing up `zeev.db` first. This was a one-time data fix, not something the code change alone could undo.
  - Pinned by `tests/test_call_outcome.py`.

## Version Control / Deployment

Never commit data files (e.g. `adult_jokes.json`, imported corpora) unless asked; add generated/data files to `.gitignore` by default. Watch for CRLF line endings from copy-paste in shell scripts/sudoers. Before running `./deploy.sh`, ALWAYS commit local changes first — it relies on `git push origin main`.

## Running

```bash
python3 zeev/zeev.py           # terminal REPL
python3 zeev/zeev.py --web     # web server HTTP port 5000
python3 zeev/zeev.py --https   # web server HTTPS port 5443
python3 zeev/zeev.py --device  # Whisplay HAT device mode
python3 zeev/zeev.py --call <NUMBER> [--intent "TEXT"]
```

Requires `GROQ_API_KEY` and `TAVILY_API_KEY` in `.env` (loaded via plain-text parser, no python-dotenv). Only stdlib + `requests` needed.

## Architecture

Single-file app: `zeev/zeev.py`.

**Key non-obvious behaviors**:
- **`_groq_post`** — per-model 429 cooldown: `_groq_model_rate_limited_until` dict (model_id → epoch, 5-min backoff) so a 70B limit doesn't block 8B calls. Torah/70B/GPT-OSS use 1200 max_tokens; 8B uses 600.
- **`_groq_post_with_fallback`** — wraps `_groq_post`; on 429/cooldown, tries OpenRouter's free tier via `_OPENROUTER_FREE_CANDIDATES`, a short ordered list of specific chat models, one at a time until one returns HTTP 200. Used by device-mode chat, web `/chat` SSE, thermal SSE, detail prefetch — not vision (no free equivalent) or the 413 trim-retry loop. `_llm_post`'s streaming path has its own longer OpenRouter→Gemini→bosgame chain.
  - **Never use `"openrouter/free"`** (OpenRouter's own random-selection router across its *entire* free catalog, not a chat-specific alias) — live 2026-08-06, it picked a content-safety classifier model, and its "User Safety: safe / Response Safety: safe" classifier output was returned and spoken to Alex as a real answer to a Torah question, because it's syntactically a valid completion and nothing checked its shape. Two prior commits had drifted this constant from specific per-model slugs to that random router, each time OpenRouter retired the previously-hardcoded slug — the free-tier catalog has turned over entirely since this project started (none of the original three slugs exist anymore), so a single hardcoded model isn't durable either. `_OPENROUTER_FREE_CANDIDATES` tries a short list in order for exactly this reason — measured live, 2 of 4 reasonable candidates were already 429'd from shared-pool rate limits at the same moment.
  - Also fixed in the same pass: `if not or_err` only catches a connection-level failure — `_openai_compat_post` returns `(resp, None)` even on a 429/error HTTP status, so a rate-limited OpenRouter response was being silently accepted as a successful completion. Now also checks `resp.status_code == 200`. Pinned by `tests/test_openrouter_fallback.py`.
  - **A second live incident the same night cut the candidate list down to one entry, and why matters for adding more later.** The first fix's second candidate, `nvidia/nemotron-nano-9b-v2:free`, answered correctly non-streaming but in streaming mode — what device mode actually uses — puts its entire output under a `delta.reasoning` field and leaves `delta.content` permanently empty; `_iter_llm_tokens` only ever reads `delta.content`. Result: a real live turn ("tell me about Ezekiel") got Zeev's fallback to succeed at the HTTP level, then go completely silent (`[stream] empty stream — no reply text`). Checked several other current free models the same way (raw streaming curl, not a non-streaming test): `openai/gpt-oss-20b`, `cohere/north-mini-code`, `inclusionai/ling-3.0-tiny`, `poolside/laguna-s-2.1` are all the same reasoning-only shape — the free-tier catalog currently skews heavily toward reasoning models. Only `google/gemma-4-26b-a4b-it:free` is confirmed to stream real text in `delta.content`. **Any future addition to `_OPENROUTER_FREE_CANDIDATES` must be verified with a raw streaming request first** — a non-streaming test alone would have missed this exact bug, which is how it got added the first time.
- **`_bosgame_stream`** — uses `http.client.HTTPSConnection` directly (not `requests`) to avoid urllib3 pool conflicts. Connect timeout 10s; socket timeout extends to 300s after connect for slow CPU inference.
- **`_llm_post`** — on connection errors, prints `[offline]` and falls back to `_bosgame_stream()`. Returns `(resp, err, provider)`.
- **`extract_memory`** — prefers `_bosgame_complete` (llama3.2:1b, ~5–10s) over Groq; falls back on error.
- **`route_model`** — `_REASONING_RE` → GPT-OSS 120B; `_SMART_RE` → 70B; default → 8B. No extra API call. **The gate matters more than the model here**: keyed on written-maths vocabulary, it sent *zero* of eight ordinary spoken word problems to the reasoning model (six went to 8B), because speech rarely says "calculate" or "theorem". It now also matches spoken arithmetic, percentages, "how many/much" *with a digit present* (so "how many nieces do I have" stays recall), and elapsed-time questions. Routing there is free — GPT-OSS answered in 0.4s, faster than the 70B it otherwise fell to. Pinned by `tests/test_model_routing.py`, negatives included.
- **`zeev_cleanup()`** — kills `_MUSIC_PROC`/`_piper_term_proc`, `pkill -f` for `zeev_music`/`zeev_rec.wav`/`piper --model`/`mpg123`, removes `/tmp/zeev_*`. Registered via `atexit` so it fires on unhandled exceptions too, not just SIGINT/SIGTERM.
- **`run_web_server`** — `ThreadingHTTPServer`. Endpoints: `/chat` (SSE stream), `/clear`, `/memory`, `/memorize`, `/tts` (POST text → WAV), `/transcribe` (raw audio → transcript), `/thermal` (SSE), `/thermal-status`, `/volume` (GET/POST), `/snap`, `/gps`.
- **`_build_system_prompt`** — assembles base persona + memory facts + RAG hits + optional calendar + optional Tavily results. `needs_weather(text)` (subset of `needs_search`) appends a units instruction to spell out `°F`→"degrees Fahrenheit" / `mph`→"miles per hour" in full words, since replies are spoken via TTS.
- **SQLite** (`data/zeev.db`, WAL mode): tables `messages`, `facts`, `notes`, `settings`, `quantum_insights`. `_db_lock` guards all writes (thread-safety for ThreadingHTTPServer).

### Models (Groq)

| Key | Model ID | Use |
|---|---|---|
| `1` | `llama-3.1-8b-instant` | Fast — casual chat |
| `2` | `llama-3.3-70b-versatile` | Smart — code, writing |
| `3` | `openai/gpt-oss-120b` | Reasoning — math, logic |

### Key constants

| Constant | Value |
|---|---|
| `PRIOR_TURNS` | 15 turns loaded from DB per session |
| `max_tokens` | 600 (8B) / 1200 (70B, GPT-OSS, Torah) |
| `temperature` | 0.75 |
| `VISION_MODELS` | OpenRouter free tier (see below) — **Groq dropped vision** |

### Vision

**Groq no longer serves any vision model** (verified 2026-07-30 against `/v1/models`: 15 models, none accept images), so the old `_groq_post(VISION_MODEL)` 404s and every camera path that used it was dead — the Pi camera, web `/snap`, and terminal `/look`. All now go through **`vision_complete(image_b64, question)`**, which walks `VISION_MODELS` on **OpenRouter's free tier** in order, because a 429 or a stall on a free endpoint is routine rather than exceptional.

**Re-benchmarked 2026-08-08** against OpenRouter's then-current free-tier vision catalog (only 5 models had `image` in `input_modalities`; `nvidia/nemotron-3.5-content-safety:free` excluded outright — a classifier, not a scene-description model, same trap as the `openrouter/free` router incident below). Tested with the real `_VISION_HONESTY` guardrail text appended (raw requests without it are not representative — see next paragraph). `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` won on both axes: 0.3-0.6s vs `gemma-4-26b`'s 1-5.4s, and equally or more detailed, correctly hedging on illegible text rather than guessing. `google/gemma-4-31b-it:free` was 429 both times tried; `nvidia/nemotron-nano-12b-v2-vl:free` failed outright past 120s. Current order: `nemotron-3-nano-omni-30b-a3b-reasoning:free` → `gemma-4-26b-a4b-it:free`.

- **The honesty guardrail isn't optional context for this benchmark — it changes which model looks safe.** An unguarded raw test (no `_VISION_HONESTY` suffix) of the same photo had `nemotron-3-nano-omni-30b` confidently invent a different wrong name off an illegible diploma on 2 of 3 tries ("Alexander Kline", "Alexander Kizzy", "Alexander Kagan" — none correct), stated as fact, no hedge. `gemma-4-26b` never did this — it just said "certificate"/"plaque" without guessing content. Once the real guardrail text was included, both models correctly reported the text as illegible instead. **Any future vision-model benchmark must include `_VISION_HONESTY` in the test prompt**, or a model that only behaves under instruction will look worse than it performs in production, and a model that hallucinates only when unguarded won't get caught at all.

- Needs `OPENROUTER_API_KEY`. It was on the Pi but **missing from the dev checkout**, so the OpenRouter fallback CLAUDE.md documented had never actually run locally.
- Web `/snap` no longer streams the vision reply — it sends one `token` event. A half-streamed reply from a model that then 429s can't be taken back.
- `deepseek-r1-distill-llama-70b` is also gone from Groq, but `MODELS["3"]` had already been migrated to `openai/gpt-oss-120b` — only these docs were stale, which is exactly how a phantom bug gets reported. Read the table in `zeev.py`, not here, when a model id is in question.

### Wyze cameras

House cameras reach the same vision path as the Pi's own eye. Full incident history (bridge-vs-flashed-firmware saga, camera-gate hallucination bugs, credential leaks, flashing recovery) moved to `docs/wyze-cameras.md` — read it before touching camera code.

**Current state**: two cameras (`smokeys-cam` at 10.0.0.217, `bedroom-cam` at 10.0.0.84) run Wyze's own RTSP firmware directly (`/stream0`, not `/live`) and work; the other six are bridge-blocked on a hardware DTLS handshake issue with no software fix, and only two cameras can safely take the flashing route (see `docs/wyze-rtsp-flashing.md`).

**Non-obvious gotchas that bite immediately**:
- `_scrub_rtsp()` before logging anything from ffmpeg — it echoes the password in every error line.
- `resolve_wyze_cam()` asks rather than guesses; a missed camera gate does NOT fail safe — the 8B has invented entire fabricated camera feeds before.
- Never put a camera password in a URL or shell command (`%`/`@` characters break `printf`/URL parsing) — `WYZE_RTSP_USER`/`WYZE_RTSP_PASS` are plain values, percent-encoded by `wyze_stream_url()`.

### Named subjects ("check on Smokey")

A pet or person Zeev can be asked about **by name**, sweeping cameras until it finds them. `ZEEV_SUBJECTS=smokey|smoky|smokie:cat:basement-cam|upstairs` (comma-separated entries, `name[|alias…]:kind[:cam|cam]`, same unquoted shape as `OWW_VOICE_MAP`). Cameras default to those with a direct RTSP URL, capped at `ZEEV_SUBJECT_MAX_CAMS` (3). **`.env` is not in git**, so this line has to be added on the Pi itself — without it `WYZE_SUBJECTS` is `{}`, the gate never matches, and the turn falls through to the LLM, which confidently says it can't see Smokey.

- **Name aliases exist because Whisper spells names however it hears them** (the same hazard `_WYZE_CAM_RE`'s comment records for room phrasing). A missed alias fails *silently* — first alias is the one Zeev speaks.

- **`kind` is what the vision model is asked about, never the name.** "Is there a cat in this image" is judgeable; "where's Smokey" invites the model to narrate a shadow as a resting cat — it cannot know which cat is Smokey and will not say so. The name is substituted back into Zeev's reply.
- **The branch sits above the tool branch, so `resolve_subject()` rejects `_TOOL_INTENT_RE` phrasing outright.** "Remind me to check on Smokey at four" is a reminder; without the guard the camera sweep swallows it. Trigger must also appear in the first 60 chars with the name within 40 after it (the `_bt_call_match` shape) — a bare name mid-sentence is far too common.
- **`parse_subject_sighting()` is three-state: yes / no / `None`.** Free-tier vision ignores the `FOUND:` format routinely. Folding unparseable into "no" burns the next camera and then denies the sighting while holding the description that made it; `None` reports the description as uncertain instead.
- **Camera list must not default to all of `WYZE_CAMERAS`** — six of eight never answer, so an unlisted default spends `WYZE_SNAP_TIMEOUT` on each before speaking.
- **Next grab starts under the current vision call** (grab 4–8s vs vision ~21–25s), so a two-camera sweep is ~38s rather than ~58s. Wasted work on a hit is one background ffmpeg.
- A miss is worded **"I didn't see Smokey on …"**, never "he isn't there" — a small model missing a dark cat on a dark couch is the wrong-city failure class. Zero frames reports the cameras as asleep/offline instead, which is a different answer.
- Speaking *through* a camera is **not possible**: the RTSP firmware is outbound-only (no ONVIF backchannel; v3 isn't ONVIF), and docker-wyze-bridge closed audio-out as `wontfix`. A BT speaker in the room is the route if this is ever wanted.
- Pinned by `tests/test_wyze_subjects.py` (config, gate, verdict parsing) and the subject-sweep block in `tests/test_handle_transcript.py`.

### Reminders / timers (LLM tool calling)

Zeev's only write capabilities. `reminders` table (`text`, `due_ts` epoch, `fired`); `_reminder_loop()` polls every 20s and speaks what's due.

- **`due_reminders()` claims and marks fired in one transaction** — the poll loop can overlap itself and a reminder announced twice is worse than one announced late.
- **Announcement** waits for `_face_state` to leave `thinking`/`speaking` (up to ~2 min) so it never talks over a live turn, then restores the prior state. Device mode sets `_reminder_notify[0]`; web/terminal store reminders but don't speak them.
- **Tool calling is deliberately narrow.** The 17 regex gates stay the fast path — a tool round trip before dialling would be worse UX. Tools cover only actuators that had no gate at all: `set_reminder`, `list_reminders`, `cancel_reminder`, `add_note` (which existed but was unreachable from the device).
- **`_TOOL_INTENT_RE` pre-gates the tool path** so ordinary chat never pays for the extra non-streaming round trip (tool calls only arrive complete, so that request can't stream).
- **Routed to 70B**: the 8B default is unreliable at emitting well-formed tool calls.
- **The model is given the wall clock** in the tool system prompt — nothing else in the prompt provides it, so "at 4" was otherwise unresolvable. `_parse_when` also accepts `"in 10 minutes"` because models emit relative forms regardless of the schema, and returns `None` on anything unresolvable so the model re-asks instead of believing it succeeded. A bare time already past rolls to tomorrow.
- **Calendar write is NOT implemented**: `data/gcal_token.json` holds only `calendar.readonly`; widening it needs interactive Google consent (re-run `zeev/gcal_auth.py`).
- Verified live on the Pi: *"remind me to call Dave at 4"* → `set_reminder{"when":"2026-07-28T16:00:00"}` → row created.
- **The tool round trip was only ever wired into device mode — web `/chat` and the terminal REPL had no version of it at all** (found 2026-08-05 via `rag_probe.py`'s first run: "What are my reminders?" over the web UI got answered with a wholly invented reminder, because `_build_system_prompt` never injects live reminders and nothing else in that handler could ground the answer). Fixed in both: the identical `_TOOL_INTENT_RE` gate + non-streaming `_groq_post(..., tools=_TOOLS)` round trip was added to the `/chat` handler (`run_web_server`, before the streaming completion) and to `stream_reply()` (before its own completion call) — `stream_reply` is the one function both terminal call sites (the plain chat loop and the `/thermal <question>` follow-up) go through, so patching it there covers the whole REPL in one place.

### Google Calendar

`gcal_fetch`/`gcal_create_event` (OAuth2, `calendar.events` scope for writes) plus `_gcal_reminder_loop()` (autonomous reminders, reconciles into the `reminders` table every 30 min). Full incident history (birthday-digest bucket-key bugs, routine-event filtering) moved to `docs/google-calendar.md`.

**Non-obvious gotchas that bite immediately**:
- The reminder filter is exclusion-based on purpose — an allowlist of category words fails on this real calendar (appointments are titled things like "Haircut w/Julie", never "appointment").
- Birthdays are checked FIRST in the filter, load-bearing, not style — both `eventType=birthday` and hand-typed ones need their own check.
- `_utcnow()`, not `datetime.utcnow()` — deliberately naive (token file/timeMin/timeMax expect that), don't "fix" it to be timezone-aware.

### GPS / geolocation

Tiered pipeline: WiFi AP triangulation (Google Geolocation API → beacondb) → IP fallback (`ip-api.com`). `gps_locate()` cached 30 min. `_reverse_geocode` via Nominatim/OSM. `/gps` terminal command; `GET /gps` web endpoint.

**Three things had to be true before triangulation worked at all** (all found live 2026-07-30; before them every fix silently fell through to IP — 25 km, naming Ashcroft/Millbrook for a device in Fairview):

- **`GOOGLE_GEOLOC_KEY` must be in the *Pi's* `.env`.** It was only in the dev checkout, so the Google branch never ran. beacondb is not a substitute — it answered `"fallback":"ipf"` for these APs, i.e. no coverage, which `_wifi_geolocate` correctly rejects.
- **`nmcli dev wifi list` reports NetworkManager's *cached* scan.** Nothing asked for a fresh one, and on an idle Pi the cache held **one** AP against a two-AP minimum. An explicit `nmcli dev wifi rescan` takes it to 7. Rescan is privileged and the service runs as `ragnar`, so it needs `/etc/polkit-1/rules.d/50-zeev-wifi-scan.rules` (on the Pi, outside the repo; scoped to `org.freedesktop.NetworkManager.wifi.scan` only). Without the grant the cached list is still used, so it degrades rather than breaking.
- **`signalStrength` is dBm, not nmcli's percent.** Passing the percentage through is silently accepted and just makes the fix worse: same 7 APs measured **869 m with percent, 11 m with dBm** (`_nm_percent_to_dbm`, NM's own `2*(dBm+100)` inverted).

Result end-to-end: `[gps] fix: Fairview, Massachusetts, United States (±11m via wifi+google)`.

**Ambient awareness**: `_build_system_prompt` always appends `## Right now: <local time>` and `## Approximate location: <City, Region, Country>`. Before this the model saw location only when `needs_gps` matched — so it was blind to it on every other turn, and weather answers were location-aware only by accident, via a memorised fact naming the town (which goes stale on travel).

- The location block is **coarse on purpose** — no lat/lon, no accuracy — because it goes to Groq and OpenRouter on *every* turn; `gps_summary()` with coordinates stays behind `needs_gps`. `ZEEV_AMBIENT_LOCATION=0` disables it.
- Above `_AMBIENT_CITY_MAX_ACC` (5 km) it reports **region only**: an IP fix names the wrong town and the model repeats a wrong city as fact.
- The read path (`gps_cached()`) is **cache-only and never scans** — a cold `gps_locate()` is ~1.7s with the rescan, which must not sit in a turn. `_gps_refresh_loop` warms it every 20 min, deliberately **under** the 30-min TTL, or a cache-only reader keeps finding it expired. It also reverse-geocodes in the background, since a WiFi fix carries no place name and the block is nothing but place names.
- **The wall clock was missing from every prompt except the tool prompt.** With no clock the model confabulates instead of declining: observed 2026-07-29 21:02–21:04 answering "8:45 AM", then "2:45 PM" (inventing a calendar reading to justify it) while the Pi read 21:03 EDT. `_now_str()` uses `.astimezone()` — `datetime.now()` is naive, so `%Z` formats as empty and the zone vanishes while the string still looks fine.

**Street/POI/home-level vicinity** (`vicinity_place()`, added 2026-08-02): "on Lincoln Street" / "near Main Plaza" / "at home", built into `gps_summary()`, which stays behind `needs_gps` — never in the every-turn ambient block, which is coarse on purpose.

- `_reverse_geocode` moved from zoom=14 to **zoom=18** (building level) to get `address.road` and a POI `name` ("Main Plaza") at all — zoom=14 never returned them, only suburb/city. Verified live against Nominatim that `city` is identical at both zooms, so this doesn't touch the ambient block.
- Gated on `_VICINITY_MAX_ACC` (50 m), separately from `_AMBIENT_CITY_MAX_ACC` — Nominatim returns the nearest road for *any* coordinate regardless of fix quality, so an IP fix (~25 km) or a poor beacondb fix would otherwise name a street with no basis. A missing `accuracy` also fails toward silence, not toward asserting — the opposite default of most fields in this module.
- **Home is opt-in via `ZEEV_HOME_LAT`/`ZEEV_HOME_LON`/`ZEEV_HOME_RADIUS`** (metres, default 100) in the Pi's `.env` — not in git, same as `GOOGLE_GEOLOC_KEY`, so without it "am I at home" degrades to a street name forever, never to a wrong "no". Parsed via `_env_float()`, which skip-and-logs a malformed value rather than crashing the whole app at import — same constraint as `parse_subjects()` for `ZEEV_SUBJECTS`. When home is configured and the fix is within radius it wins outright over naming the street, since that's the answer that's actually useful for a fix sitting in the driveway.
- The three call sites (prompt block, `/gps`, the refresh loop) used to each gate their own `_reverse_geocode` call on `not loc.get("city")` — meaning once the refresh loop filled in `city`, later sites never called it again, so `road`/`poi` (added after `city` already worked) could never populate on an already-warm cache entry. Unified into `_geocoded_location()`, gated on a `_geocoded` sentinel (tried-once, not "succeeded"), enriched dict written back to `_gps_cache` so it's shared.
- `_GPS_RE` widened for `(what|which) (street|road) am i on`, `am i at home` — the coarser `where am i` phrasing already matched, but street/home-specific asks didn't, which would have been the enrichment landing with no gate to reach it (the angelic-prayer failure shape).
- The road claim is worded **"approximately on X Street"** — the one claim here without a firm basis like a home radius or a named POI, and the model restates prompt-block text as settled fact, so the hedge has to live in the words themselves.
- Pinned by `tests/test_gps_vicinity.py`.

### User memory

`facts` table in `zeev.db`, injected unconditionally under `## What I know about Alex:` on every turn. `extract_memory()` runs on `quit`/`/memorize` (terminal/web) **and automatically every 5 turns in device mode** (`finish_turn`, backgrounded). 429 returns `None` — shows a warning instead of fake success. `/forget-fact N` to remove.

- **Extraction used to have no check on who actually said what — it would extract Zeev's own invented statements as user facts.** Found live 2026-08-06: `USER_FACTS` had accumulated "Alex's nieces live in New Rochelle" and "...will be flying to London" sitting right next to the real "Alex's nieces' names are Hannah and Pearl" — all three extracted together because Zeev asserted all three in one breath during an earlier fabrication (same class as the confabulation-under-ambiguity incidents in `docs/rag-probe-findings.md`). Since `USER_FACTS` is unconditionally injected every turn, this meant Zeev wasn't re-hallucinating those details each time — it was correctly *recalling* a poisoned memory. **Fixed**: the extraction prompt now explicitly scopes to what the USER said, instructing the model not to trust Zeev's own assertions unless the user's own words confirm them. Pinned by `tests/test_memory_extraction.py`.
- **The merge/dedup check was exact-string membership, so re-extraction routinely re-added the same fact under a reworded subject** ("Alex's X" vs "The user's X") — found live at 91 stored facts, ~35-40 actually unique. **Fixed**: `_fact_key()` normalizes subject phrasing before the dedup comparison. One-time cleanup on the Pi (2026-08-06, after backing up `zeev.db`): 91 → 50 facts (9 confirmed fabrications removed, 32 reworded duplicates collapsed).
- **`facts` has no timestamp column at all — so a genuinely real but temporary state gets stored exactly like a permanent one, with no way to ever tell the model it's gone stale.** Found live 2026-08-07, a month after the Uncle Sasha visit above ended: Zeev was asked to clarify an unrelated prior reply ("They're leaving. The car will be warm for a while.") and confidently invented "I was referring to Uncle Sasha and the summer visitors... heading back home today" — pure fabrication about its *own* prior turn, but pulling a real detail ("visiting Uncle Sasha... leaving today") straight from `USER_FACTS`, which still held it unchanged from 2026-07-12 with zero staleness signal (unlike history-RAG hits, which get a `_message_date_str` caveat — this block never did). **Fixed at the source rather than by dating every fact**: `extract_memory()`'s prompt now explicitly excludes one-time events and anything framed as temporary/day-specific ("today", "this weekend", "currently") from being extracted as a durable fact in the first place — `USER_FACTS` is for identity/relationships/preferences/possessions/skills/standing habits, not "what's happening this week." A dating scheme was considered and rejected: most stored facts (has a cat, speaks Hebrew, lives in Canton) are genuinely permanent and don't need one, and stamping every fact with a date whether it needs it or not just adds prompt noise for no benefit on the facts that don't expire. Pinned by `tests/test_memory_extraction.py`. One-time cleanup on the Pi (2026-08-07, same backup discipline): removed 7 stale Uncle-Sasha-visit facts, including one that appears to have been re-extracted from the hallucinated reply itself, reinforcing the same bad detail — 51 → 44 facts.

### History RAG

Two layers. `_build_system_prompt` calls `retrieve_semantic(...) or retrieve_relevant(...)` — semantic first, keyword as the offline fallback — and injects the winner as `## Relevant past exchanges:`.

- **Semantic (preferred)**: `embed_text()` → bosgame `nomic-embed-text` via the existing `/ollama` proxy (`{BOSGAME_URL}/api/embeddings`, `X-Zeev-Key`). Vectors cached in the `message_vecs` table (`message_id`, `dim`, `vec` BLOB of little-endian f32), so retrieval is a local dot product — only the query embedding is a network call. Embedding runs remotely for the same reason Russian TTS does: the Pi has one usable core and ~200 MB headroom. Measured backfill rate: **~1.4 msg/s**. `EMBED_MODEL` overrides the model; vectors whose `dim` doesn't match the query are skipped, so switching models degrades instead of corrupting.
- **Keyword (fallback)**: `build_rag_index()` — last 500 rows, inverted index, stop words filtered; `retrieve_relevant(query, k=2, min_score=2)`. Its `_tokenize` is `\b[a-z]{3,}` — **ASCII-only, so Hebrew/Russian/Spanish history was never indexed at all**, and it can't see past 500 messages. Both are why the semantic layer exists. Kept because `embed_text()` returning `None` is a normal state for a travelling Pi.
- **Language guard**: because semantic recall can now surface Hebrew/Cyrillic into an English turn's prompt, `_build_system_prompt` appends an explicit "reply in English regardless" note when a hit contains those scripts and no `FORCED_LANG` is set. The 8B model flips script easily — see the transliteration notes above.
- **`_memory_maintenance_loop()`** (started by `init_learning()`): every 30 min, backfills up to 50 new message vectors and re-runs `load_latest_reflection()`. Both were startup-only before, so a device up for weeks never saw a new weekly reflection and post-boot messages were never embedded.
- **`save_memory()` is transactional** (`BEGIN IMMEDIATE` + rollback). It previously did `DELETE FROM facts` then re-INSERT outside a transaction — an exception or power cut between the two lost every fact. Pinned by `tests/test_semantic_memory.py`.
- **Recency-weighted, deduped ranking** (2026-08-04, `docs/research-directions.md` #1): both `retrieve_semantic` and `retrieve_relevant` rank by similarity/keyword-score **blended** with `_recency_factor(ts)` — an exponential decay, half-life `RAG_RECENCY_HALFLIFE_DAYS` (30d default), fails open to 1.0 on a missing/bad timestamp. It's a tie-breaker, not a filter: `min_sim`/`min_score` still gate on the **raw** score, so a recent-but-irrelevant hit can never outrank a relevant-but-old one just because it's recent. `retrieve_semantic` additionally skips any candidate whose vector is a near-duplicate (cosine ≥ `RAG_DEDUP_SIM`, 0.97 default) of a hit already picked — the same question asked on two different days no longer spends the whole `k` budget on itself twice. Pinned by `tests/test_rag_reranking.py`.

### Torah RAG (Sefaria)

FTS5 DB `data/torah.db` (Tanakh, Mishna, Talmud, Apocrypha, Siddur/Haggadah, Zohar, DSS, Sumerian), populated by `zeev/import_sefaria.py`. `needs_torah(text)` gates it; `torah_search(query, k=3)` returns `(ref, en, he)`. Full incident history (the four-bug "angelic prayer" saga, ref-indexing gotchas, passage-truncation fix) moved to `docs/torah-rag.md`.

**Non-obvious gotchas that bite immediately**:
- **`ref` is `UNINDEXED` in FTS5 — only `en` is searchable.** A passage findable by title alone (most of the siddur) needs `_torah_ref_lookup()`'s `LIKE` probe, not `MATCH`.
- Every Torah/parsha call site must force `MODELS["2"]` (70B) + `max_tokens >= 1200` — the 8B's 6k TPM limit cannot carry a full passage. There are 5 call sites; a missed one silently truncates.
- `MAX_PASSAGE_CHARS = 20000` (raised from 4000) — a plain re-run of `import_sefaria.py` will NOT pick up a cap change; the `done` table must be cleared first.

### Weekly reflection

`zeev/weekly_reflection.py` — synthesizes the last 7 days of messages into a first-person Zeev reflection. Stored in `reflections` table; injected into every system prompt under `## Weekly reflection:` (capped 1500 chars). LLM: bosgame `llama3.1:8b` first, Groq 70B fallback. Cron: `0 7 * * 0`. Skips if fewer than 10 messages in the window.

### RAG-faithfulness dashboard

`zeev/rag_probe.py` measures whether Zeev's two RAG systems (Torah, history) stay grounded in what they retrieve rather than paraphrasing into invention. Runs real questions through the real production path, grades answers against exactly what was retrieved, logs to the `rag_probes` table. `--report` shows the rolling 30-day grounded rate. Wired up on the Pi via `zeev-rag-probe.service`/`.timer`, staggered between quantum-daily and weekly-reflection.

Eight runs' worth of findings (mostly probe-grading gaps — the grader wasn't shown the Torah/location/persona blocks that a real answer could legitimately draw from — plus a few genuine production bugs: fabricated reminders, an unscoped persona trait bleeding into factual answers, a stale-conversation-presented-as-current bug) are in `docs/rag-probe-findings.md`. Read it before investigating a new UNGROUNDED finding — the fix is very often "the grader is missing context," not "the model hallucinated."

### Quantum reasoning

`quantum_reason(idea, llm_fn, past_insights=None)` in `zeev/quantum.py`: idea → circuit spec → simulate → interpret. `past_insights` (k=3 most recent) compound learning over time. `quantum_daily.py` — 8 human-dilemma scenarios, one per day. Cron: `0 6 * * *`.

### Music playback

`youtube_play(query, adev=None)` — `yt-dlp --default-search ytsearch1` → ffmpeg → mpg123.

- **Device mode**: `extract_music_query` (play) and `_MUSIC_STOP_RE` (stop) are gated in `_handle_transcript`, placed **after** the visual gates so "play the fire effect" stays a visual request instead of becoming a search for a song. Before this, music was reachable only from the web UI and terminal — and natural-language *stop* did not exist anywhere, only a `/stop` slash command.
- Zeev says "Looking for X", then resolves in a **background thread**: yt-dlp search + format selection measured **~45s** on this Pi, and blocking the turn that long left the device unusable and deaf to a follow-up.
- **yt-dlp must be current.** The apt build (`/usr/bin/yt-dlp`, 2025.04.30) could no longer extract audio at all — YouTube's SABR change made it report "Only images are available". Fixed by installing the upstream binary to `/usr/local/bin/yt-dlp` (which precedes `/usr/bin` on PATH). If music silently stops working, check `yt-dlp --version` first.
  - **Kept current automatically**: `/etc/cron.d/yt-dlp-update` runs `/usr/local/sbin/yt-dlp-update.sh` weekly (Sun 04:00) — `yt-dlp -U` plus a timestamp, logged to `/var/log/yt-dlp-update.log` (trimmed to 200 lines). Both live on the Pi, outside the repo. Note `-U` self-updates the binary in place, so it must run as root; a *user* crontab entry would need `sudo` and silently fails without it. Also note `/etc/cron.d` filenames may not contain a dot, and the file must be mode 644.
- **`AudioClient.play()` returns `(title, error)`.** It used to return just a title with a 30s timeout and the standard one-shot retry, which was wrong three ways on this hardware: 30s < the ~45s resolve, so it always timed out; the retry re-sent the same request and started a *second competing download*; and the failure surfaced as an empty title that `youtube_play` turned into `(query, None)` — so the device cheerfully said "Playing X" while nothing played. Now 150s, no retry, and errors propagate.

### Multilingual TTS

| Language | Detection | Terminal/device | Web UI |
|---|---|---|---|
| English | default (always) | Piper `en_US-lessac-medium` | Groq Orpheus `daniel` |
| Hebrew | `/lang he` (terminal/web) or voice (device) | gTTS + mpg123 | gTTS MP3 → `speechSynthesis` `he-IL` |
| Spanish | `/lang es` (terminal/web) or voice (device) | **device**: bosgame remote Piper `es_AR-daniela-high` (female, Argentina) first, local Piper/gTTS fallback; **terminal**: gTTS + mpg123 | gTTS MP3 → `speechSynthesis` `es-MX` |
| Russian | `/lang ru` (terminal/web) or voice (device) | **device**: bosgame remote Piper `ru_RU-irina-medium` (female) first, local Piper/gTTS fallback; **terminal**: gTTS + mpg123 | gTTS MP3 → `speechSynthesis` `ru-RU` |

`detect_lang` no longer auto-detects from character sets — always returns `FORCED_LANG or "en"`. Terminal/web change language via `/lang` only — device mode has no `/lang` handler, so it uses `lang_switch_intent()` (`zeev.py:720`): requires a speak/talk/switch/say verb within 20 chars of the language name (guards "I have a Russian friend"). Wired into `_handle_transcript()` after the voice-coach intent block; sets `FORCED_LANG`, speaks a confirmation. Groq Orpheus is English-only. `/tts` endpoint tries gTTS before returning `503 {"lang": ...}` for browser `speechSynthesis` fallback.

**Russian/Spanish TTS route to bosgame, not local Piper**: measured RTF on the Pi Zero 2W ARM core (~2.6x Russian / ~1.4x Spanish, 11-20s for a short reply) vs bosgame's x86 CPU with identical models (~0.135x, ~1s) — the Pi's CPU is the bottleneck, not the model. `_speak_device()` step 0 loops over `_REMOTE_PIPER_LANGS = ("ru", "es")`, tries `_audio.speak_sync(lang=...)` first (daemon → bosgame's dedicated Piper model — Kokoro doesn't support either), falling to local `_piper_direct()` (step 0b) only if the daemon is unavailable/fails. feiergente01 has neither model, so `speakPiper` keeps `lang in ("ru","es")` chunks on bosgame only. `PIPER_MODELS["ru"]` (local fallback) prefers `ru_RU-irina-medium.onnx` (female) over `-dmitri` if both present.

**Local Russian Piper fallback** (only if daemon/bosgame unreachable): chunks via `_gtts_chunks()` + per-sentence `_piper_direct()` so playback starts after sentence one — one-shot synthesis at RTF ~2.6x would leave 30-60s of dead air. `_PIPER_RU_MAX_WORDS` (35) skips Piper for longer replies. Only a first-chunk failure triggers gTTS fallback, not mid-reply (avoids re-speaking already-played sentences).

**LLM must output actual Cyrillic, not transliteration**: the 8B model reliably romanizes Russian and adds English glosses even when told "Reply in Russian only" — a prompt-compliance issue, not a synthesis bug (Piper's Cyrillic phonemizer mispronounces Latin-letter input). `_LANG_INSTRUCTIONS["ru"]`/`["he"]` (`zeev.py:4364`) forbid transliteration/English parentheticals. If garbled/wrong-language audio recurs, check the raw LLM reply text (`journalctl -u zeev-device | grep 'Zeev \[8B\]'`) before assuming a TTS bug.

### Adult jokes

`/joke` feature (`random_joke()`) serves from static, pre-curated `data/adult_jokes*.json` pools (one per language, git-ignored data files) — not LLM-generated. `_JOKE_EXCLUDE_RE` filters Jewish/Israeli/religious topics at `load_jokes()` time (this household's own exclusion policy, not a general filter).

- **`zeev/joke_probe.py`** — quality/safety QA harness, same "fresh-context LLM grader, log to a table, `--report`" shape as `rag_probe.py`. Grades sampled jokes on four axes (funny/punchline/dirty/safe) via `_grade()`, which tries feiergente01's local `dolphin3:8b` (via `_feiergente_complete(model=...)`) first, falling back to Groq's `llama-3.3-70b-versatile` with retry-with-backoff. **Groq's free tier has a 100k-token/DAY budget, not just per-minute** — a large grading run can exhaust it mid-audit with every subsequent retry hitting the same flat `used 99817/100000` 429 regardless of backoff length (confirmed by reading the raw error body, not guessed). dolphin3:8b has no such shared quota (local compute) and, being an uncensored fine-tune, doesn't add refusal noise on this material. `--empty-punchline-only` targets entries with a blank punchline field specifically (tells apart legitimate one-liners from truncated scrape junk). `FEIERGENTE_URL` must be in whichever `.env` runs the probe (was only ever set on the Pi's — a dev-checkout run silently no-op'd through to Groq until added there too).
- **Pool cleanup (2026-08-07)**: 2194 → 2086 entries via several probe-driven prune passes, each backed up first (`adult_jokes.json.bak.<timestamp>` on both the dev checkout and the Pi, kept in sync by hand — these are gitignored data files, `./deploy.sh`/git never touch them). Removed: non-joke junk (Reddit-thread fragments, bare usernames, section headers with no setup/punchline), forum spam (Kik/Snapchat/OnlyFans solicitations, several using Cyrillic homoglyph characters to evade text filters), jokes targeting real named individuals (a real crime/death referenced as the punchline), and — after listening to all of them read aloud through the Pi's speaker via the daemon's `speak_sync`, one at a time, before deciding — the bulk of a 35-entry unsafe-flagged batch (minors/abuse-themed content, one more real-named-individual entry, an ethnic slur). A few flagged-unsafe entries were deliberately kept after listening (e.g. an absurdist bar-joke the grader flagged for "implying non-consent" that reads as ordinary dark humor on listen) — the LLM grader's `safe` axis is a starting point for human review, not an auto-prune signal.
- **Sexual vs. other-adult routing** (2026-08-07): `_JOKE_RE` widened to accept an optional intensifier (`super|extra|really|very|extremely`) before the dirty/adult/naughty/nasty/raunchy adjective — "tell me a super dirty joke" previously didn't match the joke branch **at all** (the regex only ever recognized a single adjective immediately before "joke"). Also switched literal `"a "` to `"an? "` in both phrase branches, which incidentally fixes a pre-existing gap where `"give me an adult joke"` never matched either. `_SUPER_DIRTY_RE` detects the intensified phrasing; `random_joke(lang, sexual=True|False|None)` restricts to `_is_sexual_joke()`'s matching subset (keyword classifier over explicit sexual-act/anatomy terms — same one used ad hoc to count the pool's ~46% sexual-content split, promoted into code). All three joke call sites (web/chat, device mode, terminal) now pass `sexual=bool(_SUPER_DIRTY_RE.search(text))`, so plain "dirty joke" always draws from the non-sexual subset (crude/profane/dark humor that isn't specifically sexual) and "super dirty" always draws from the sexual one. Falls back to the whole pool if a language's split would come back empty (a thin non-English pool shouldn't go silent over this).

### Web UI SSE events

| Event | Meaning |
|---|---|
| `{"token": "..."}` | Streamed reply chunk |
| `{"model": "8B"}` | Auto-routed model label |
| `{"info": "..."}` | Status (search in progress, etc.) |
| `{"error": "..."}` | Server error |
| `{"thermal": {...}}` | Thermal frame: `frame` (768 floats °C), min/max/center/hotspot |
| `{"image": "data:image/jpeg;..."}` | Camera snapshot |

### File layout

```
zeev/zeev.py                # entire application
zeev/audio_client.py         # Python adapter for zeev-audio daemon
zeev/mlx90640.py             # thermal camera (I2C bus 3)
zeev/import_sefaria.py       # populate data/torah.db
zeev/migrate_to_sqlite.py    # idempotent flat-file → zeev.db import
zeev/quantum_daily.py, quantum_convo.py  # daily teaching / call topics
zeev/rag_probe.py            # RAG-faithfulness dashboard
zeev/joke_probe.py           # adult joke pool quality/safety dashboard
zeev/data/                   # runtime (git-ignored): zeev.db, torah.db, adult_jokes*.json
zeev-audio/                  # Go audio daemon (cross-compiled arm64)
  cmd/zeev-audio/main.go, internal/{piper,audio,bt,music,record,server,sdnotify}/
  Makefile                  # make pi → cross-compile arm64
~/zeev-audio/zeev-audio     # binary on Pi (outside repo)
~/piper/                    # Piper TTS (outside repo); en_US-lessac-medium.onnx
```

### Thermal camera (MLX90640)

Software I2C bus 3 on GPIO5 (SDA) / GPIO6 (SCL): `dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=5,i2c_gpio_scl=6,i2c_gpio_delay_us=10` in `/boot/firmware/config.txt`. Address `0x33` on `/dev/i2c-3`. Hardware I2C `/dev/i2c-1` is used by WM8960 at `0x19`. `zeev/mlx90640.py` uses a `smbus2`-backed busio shim (Adafruit blinka can't route to bus 3 automatically).

### Device-mode turn handling

`handle_transcript(ctx, transcript)` and `finish_turn(ctx, ...)` are **module-level**, not closures. They were 654 lines buried inside `run_device_mode` (which needs the HAT to import), so the intent router — ~19 branches, the largest piece of device-mode logic — could not be imported or tested at all. `run_device_mode` went 2225 → 1597 lines.

- They stay in `zeev.py` rather than a separate module on purpose: they call ~50 module-level functions here (`route_model`, `needs_torah`, `_build_system_prompt`, `extract_bt_intent`, `run_tool_calls`, `youtube_play` …), so a new module means a circular import or prefixing every call — a large diff whose only benefit is file location.
- **`ctx.session` is the single source of truth.** The handler rebinds it (`ctx.session = ctx.session[-60:]`) while `finish_turn` appends. Two separate references would leave `finish_turn` appending to the pre-truncation list and history would silently stop growing.
- `_DeviceCtx` uses `__slots__`. Tests subclass it (a subclass without its own `__slots__` gains a `__dict__`) rather than widening the production class.
- **Voice is resolved once, at the top of the turn**, from `_WAKE_VOICE` if a wake word set it, else inferred from the transcript regex. It used to be resolved only in the LLM fallthrough, so a wake word picked the voice for chat but *not* for music/jokes/language-switch, and the unconsumed value leaked into the next turn. `finish_turn` defaults to `_LAST_VOICE` so every branch honours it. Caught by `tests/test_handle_transcript.py`, not by hand.
- **Goodnight is the one branch that answers in two voices** — Zeev (`daniel`) then Sarina (`sarina`), deliberately ignoring `_WAKE_VOICE`, since both answering is the whole point. Every other reply is spoken by `finish_turn` in a single voice, which is why it gained **`speak=False`**: the branch has already said its piece, but both lines still belong in the history. Placed high (just below the language switch, which outranks it) so no later gate can swallow a sign-off, gated to the **first 40 chars**, and excluding `_TOOL_INTENT_RE` so "remind me to say goodnight at nine" stays a reminder. A negative lookahead rejects "a good night **light**" and "a good night**'s** sleep". **Every pair in `_GOODNIGHT_LINES` names everyone in `_GOODNIGHT_HOUSEHOLD`** (Alex, Maria, Leo, Smokey) and both voices address Alex — a random choice that sometimes dropped someone would make the wish intermittent, which reads worse than not having it. Pinned by the goodnight block in `tests/test_handle_transcript.py`.

- **`detect_active_speaker()` only ever trusts a USER's own words — never Zeev's.** Found live 2026-08-07: both Zeev and Sarina answered Alex with "...thanks for asking, Maria." A since-removed branch also scanned ASSISTANT replies for a bare `"maria, "`/`" maria."` substring, on the theory that if Zeev just addressed Maria, she must be the one talking — a category error that self-reinforces. The goodnight branch above deliberately names the whole household in every reply, so one goodnight turn planted "Maria" in session history; the next turn's scan found it, misattributed the speaker as Maria, and Zeev addressed Alex as Maria — planting *another* "Maria" mention for the next scan to find. Once triggered, the loop never self-corrects, since nothing in it is the user's own words; the real message history for this incident had no genuine Maria self-identification anywhere, only Alex talking about his wife. Fixed by removing the assistant-scanning branch entirely and bounding the user-message scan to the last 6 turns, so a genuine "this is Maria" self-identification doesn't keep reassigning the speaker indefinitely after Alex has plainly resumed talking. Pinned by two tests in `tests/test_tts_pipeline.py`, one using the real incident's exact trigger text.
- **"Truncated" is inferred, and a false positive is expensive.** `finish_turn` treats a reply not ending at a terminator as evidence the model ran out of tokens: it appends **"Want to hear more?"**, speaks it, **rewrites the stored history** to the modified text, arms the follow-up listener and pre-generates a continuation. So `_last_complete_sentence()` allows closing punctuation **after** the terminator (`_SENTENCE_TAIL_RE`) — a reply ending `…to you!"` left the quote over under the old `^(.*[.!?])` and all four consequences fired on a complete reply (live 2026-08-01 19:07, logged `113/114 chars`; the missing character *was* the quote). No terminator at all still means speak it whole.
- **A follow-up "yes" must be a *bare* yes** (`_plain_affirmative()`): no pivot (`_MORE_PIVOT_RE` — but/however/actually/…) and no `?`. *"Yes, but can you sing in harmony together with Zeev?"* matched on its first word, so the canned pre-generated detail was delivered and the real question was never answered or even seen. The mirror case is fixed with it: a non-affirmative follow-up used to be **discarded outright**, so "No, what's the weather?" meant Zeev asked, Alex answered, and nothing happened — one carrying a question is now handled as its own turn, while a bare decline stays silent. Pinned by `tests/test_sentence_truncation.py`.
- **Real `finish_reason` is now logged, not just inferred** (2026-08-06, `llm_finish_log` table). The Groq/OpenAI-compatible API reports `"stop"` vs `"length"` on every completion, but `zeev.py` discarded it entirely until now — `_last_complete_sentence()` above is the only truncation signal that ever existed, and it guesses from spoken text rather than reading the API's own answer. `_iter_llm_tokens(resp, provider, on_finish=...)` now surfaces it for the `groq`/`openai`/`openrouter` SSE branch; `_log_llm_finish(path, model, max_tokens, finish_reason, reply_chars)` writes one row per completion. Wired into the three highest-traffic chat paths only (`device_chat` via `finish_turn`'s call to `ctx._stream_speak`, `web_chat` in `run_web_server`'s inline SSE loop, `terminal_chat` in `stream_reply`) — not tool calls, Torah, thermal, or any background path. Purely additive: no generation behavior changes, and a DB write failure is caught and logged, never raised into a live turn. No `--report` script yet; query `llm_finish_log` directly (e.g. `SELECT path, model, finish_reason, COUNT(*) FROM llm_finish_log GROUP BY 1,2,3`) until there's enough real traffic to make a dashboard worthwhile.
- **The pending-detail topic used to compound a "Give me more detail on that." suffix on every consecutive pre-generation failure.** Live 2026-08-06 (same night the OpenRouter fallback candidate turned out to be unreliable — see `_groq_post_with_fallback` above): asking Zeev about Ezekiel, then saying "yes" to "Want to hear more?" twice in a row while the background pre-generation kept failing, produced `"Tell me about Ezekiel. Give me more detail on that. Give me more detail on that."` as the re-asked prompt, and the second "more" reply came back a near-verbatim repeat of the first. Root cause: the fallback branch (`## Pending detail expansion`) rewrites `transcript` into `"<topic> Give me more detail on that."` for that turn's own routing/LLM call, and the rewritten value was then being stored as `_pending_detail_source[0]` for the *next* round too — so each consecutive failure appended another copy of the suffix onto an already-suffixed topic. **Fixed**: a separate `topic_for_pending` variable, initialized to the clean `transcript` at the top of `handle_transcript` and only ever reassigned to the already-clean `source` (never to the rewritten `transcript`) inside the fallback branch — `_pending_detail_source[0]` now always stores a clean topic regardless of how many rounds fail in a row. Pinned by `test_pending_detail_topic_does_not_compound_on_repeated_failure` in `tests/test_handle_transcript.py` (verified to actually fail against the pre-fix code before confirming the fix). **Not fixed by this**: if pre-generation keeps failing on every single round, the fallback still re-sends the same clean prompt each time, so near-duplicate replies are still possible — this fix stops the prompt from getting progressively longer and more garbled, it doesn't guarantee content freshness under sustained fallback failure.

### Whisplay HAT device mode

`python3 zeev/zeev.py --device`. Full incident history (streaming-TTS architecture, interrupt-handling saga, volume-curve non-linearity, wake-word tuning/retraining, VAD ceiling bug, RGB565 perf fix) moved to `docs/whisplay-device-mode.md` — read it before touching device-mode audio/wake/streaming/display code.

**Non-obvious gotchas that bite immediately**:
- **TTS priority**: Groq Orpheus → gTTS+mpg123 (he/es/ru) → Piper (en) → espeak-ng. `_stream_speak()` flushes complete sentences as the LLM generates them for fast time-to-first-audio.
- **Volume is linear in dB, not the raw ALSA register** — `_pct_to_raw()` handles this; the Go daemon's `pctToRaw` MUST match it exactly or the same percentage sounds different depending on which path is active.
- **Wake word (openWakeWord)**: `model.reset()` on every trigger is mandatory, not hygiene — a stale rolling buffer caused Zeev to answer its own noise in an endless loop once. Energy-gated (`OWW_ENERGY_MULT`) to manage CPU/thermal load; current model pair is `hey_zeev`/`hey_sarina` trained specifically to reduce false positives.
- **Utterance recording**: VAD ends turns early using the wake listener's published room-noise floor (`silence_rms`) — the daemon can't measure this itself since its first frames are already speech.

## Startup / Shutdown

- `zeev_cleanup()` runs at startup in all modes (clear crash leftovers) and is registered via `atexit` in `main()` and `run_device_mode()` so hanging Piper/mpg123 subprocesses are killed on any exit including unhandled exceptions.
- `main()` startup greeting via gTTS+mpg123 (background, ~2s). Exit: "Goodbye, Alex." synchronously before `sys.exit()`.
### Battery (PiSugar)

`_battery_monitor()` (device mode) polls `get_battery()` every 30s and does two things.

- **≤2%, not charging → shutdown.** Checked **first** and returns: the plea below waits up to ~2 min for a free device, and that wait must never sit in front of the shutdown. The plea also re-checks on return (`_battery_critical()`), since it blocks ~2.5 min unwatched.
- **≤10%, not charging → both voices ask to be charged** (`_LOW_BATTERY_LINES`, Zeev `daniel` then Sarina `sarina`, the `_GOODNIGHT_LINES` shape). Speaks via `_announce_reminder`'s path — face-state wait, `_screen_wake()`, restore prior state — not `start_health_monitor`'s bare `_speak_device`, which talks over live turns.
  - **Re-nags on a 300s cooldown, latch cleared while charging.** Firing per-poll is two pleas a minute down to the shutdown; firing once at 10% leaves eight silent percent. Clearing on charge is what re-arms the next unplug.
  - **`charging is None` pleads.** PiSugar unreadable means we can't tell, and the 2% shutdown already treats it that way. Compounding this: **the Pi's own PWR port bypasses the PiSugar**, so a charger in the wrong port never sets `charging` and the plea keeps firing — correct, and why the lines name the PiSugar port.
  - **Not gated on `_in_quiet_hours()`** — that window only lowers startup volume, and neither reminders nor health warnings honour it. Dying overnight is the worse failure.
  - `_should_plead_battery()` is module-level and pure because the monitor is inside `run_device_mode` (needs the HAT to import) — same constraint as `handle_transcript`. Pinned by `tests/test_battery_plea.py`, incl. that every line pair actually states the request.
- Journald persistent storage: `journalctl -b -1` works across reboots — but Raspberry Pi OS ships `/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf` (`Storage=volatile`), which silently reverts this and loses every prior boot. Overridden by a same-named file in `/etc/systemd/journald.conf.d/` plus `99-zeev-journal.conf` (200M, 30 days). Note journald only starts writing to `/var/log/journal` after a flush — `sudo journalctl --flush` — so a restart alone looks like the setting didn't take.

## bosgame Ollama integration

bosgame (LAN `10.0.0.141`) runs Ollama as free local inference backend.

- **Endpoint**: `https://ollama.sogdiana-gematria.net/ollama/` — Cloudflare-proxied; bosgame is the public origin for `sogdiana-gematria.net` (`/ollama/` and `/piper/` both `proxy_pass` to localhost services), so it works over the public internet, not just the home LAN.
- **Auth**: `X-Zeev-Key` header (`BOSGAME_KEY` in `.env`)
- **`.env` keys**: `BOSGAME_URL`, `BOSGAME_MODEL=llama3.2:1b`, `BOSGAME_KEY`
- **Models**: `llama3.1:8b` (chat fallback), `llama3.2:1b` (memory extraction)
- **Pi `/etc/hosts`**: dynamically managed by `/usr/local/bin/zeev-lan-hosts.sh` (systemd `zeev-lan-hosts.timer`, every 60s) — pins `ollama.sogdiana-gematria.net → 10.0.0.141` **only when bosgame answers on the LAN** (avoids NAT hairpin at home), removes the pin otherwise so the hostname falls through to public DNS → Cloudflare while traveling. Previously a static `/etc/cloud/templates/hosts.debian.tmpl` entry broke off-LAN connectivity — replaced 2026-07-26.
- **bosgame nginx**: edit `sites-enabled/default` (NOT `sites-available/default` — separate files on bosgame). `/ollama/` location: `proxy_buffering off`, `proxy_read_timeout 300s`.
## bosgame Kokoro TTS server

Primary English TTS: **Kokoro** on bosgame. Pi daemon calls `https://ollama.sogdiana-gematria.net/piper/tts`.

- **Server**: `~/piper/tts_server.py` (port 5600, localhost-only), service `piper-tts.service`. `PIPER_MODELS` maps `"lang"` → Piper model (`ru`→`ru_RU-irina-medium.onnx`, `es`→`es_AR-daniela-high.onnx`); any other/no lang uses Kokoro, with English Piper as its own error fallback.
- **Kokoro**: `~/kokoro/kokoro-v1.0.onnx` + `voices-v1.0.bin`. Default voice: `af_heart` (Sarina, 24kHz, speed=1.0). Set via `KOKORO_VOICE` env var or per-request `"voice"` field. **RTF ~0.82 on bosgame's CPU** (AMD Ryzen 5 3550H, no GPU), close to real-time; int8/fp16 quantization confirmed NOT to help this CPU (no AVX512-VNNI/native fp16 — int8 measured 3x *slower*) — fp32 is already fastest here.
- **Piper fallback**: `~/piper/en_US-lessac-medium.onnx` (22050Hz), ~0.7s latency.
- **Go daemon** (`REMOTE_PIPER_URL`): parses WAV header bytes 24-27 for sample rate. `REMOTE_PIPER_VOICE` sets default voice; falls back to `BOSGAME_KEY` if `REMOTE_PIPER_KEY` unset. Per-request `"voice"` overrides. Shared `RemotePiperClient` (10min idle timeout) + background warmup in `Init()` keep the connection warm across multi-minute gaps between turns.
- **Second backend (feiergente01)**: `REMOTE_PIPER_URL2`/`REMOTE_PIPER_KEY2` → second Kokoro instance on `feiergente01` (Windows 11, i7-1360P with **Iris Xe integrated GPU** — corrected 2026-08-06, an earlier "no GPU" note here was wrong, LAN `10.0.0.208:5601`, RTF ~0.68-0.71 measured direct-LAN). `speakPiper` alternates sentence chunks between backends (bosgame even, feiergente01 odd) — separate machines avoid same-backend contention (single-threaded `tts_server.py`). Cuts a 99-word/5-sentence reply's overhead from ~10s+ to ~3-4s.
  - **Ollama also runs on feiergente01** (`10.0.0.208:11434`), fully offloaded onto the same Iris Xe iGPU (`qwen2.5:7b-instruct-q4_K_M` installed 2026-08-06, `size_vram == size`). **This iGPU is shared, not parallel** — measured live: Kokoro TTS baseline ~3.6s for a ~5s clip degraded to 4.9s/7.8s/8.1s (up to 2.2x) across three sequential requests fired while qwen2.5 was mid-generation, and the concurrent qwen request itself exceeded 60s (vs. ~13-16s standalone for a similar-length reply). Latency returned to baseline (~2.3s) immediately once the qwen load cleared — contention, not a crash. Because Kokoro on this box is in the live `speakPiper` path, **any Ollama workload here can add multiple seconds to a real user's in-flight reply** if it happens to overlap.
  - **Guard built 2026-08-06** (`extract_memory`/`weekly_reflection.py` testing only — nothing in a live turn calls feiergente01's Ollama). `remotePiperSynthAt` in `zeev-audio/internal/server/handlers.go` touches `/tmp/zeev-feiergente-busy.lock` for the duration of any request whose URL is `RemotePiperURL2`, removed via `defer` on return (`markFeiergenteBusy`). Python's `_feiergente_busy()` (in `zeev.py` and `weekly_reflection.py`, duplicated rather than shared since they're separate entry points) checks that file's mtime and treats anything under 30s old as busy, older as a stale lock from a crashed daemon. `FEIERGENTE_URL`/`FEIERGENTE_MODEL` env vars gate the whole path — empty by default, so this is opt-in even with the code merged. `_feiergente_complete()`/`_call_feiergente()` sit ahead of the existing bosgame/Groq chain in both files and fail silently through to it on any error (including "busy"), so enabling this can only add a faster/better-quality attempt, never remove the existing fallback behavior. Verified live: correct JSON fact extraction from feiergente01's qwen2.5, lock correctly blocks while fresh and is ignored once stale (tested via mtime manipulation, not a live TTS race). **Not yet tested**: an actual concurrent live-TTS-vs-Ollama race with the guard active — only the file-mtime logic itself and the plain no-contention completion path have been verified so far.
  - **Public exposure**: `REMOTE_PIPER_URL2=https://ollama.sogdiana-gematria.net/piper2/tts`, key = `BOSGAME_KEY` (nginx rewrites to feiergente01's real key). Location is in `/etc/nginx/sites-available/ollama.sogdiana-gematria.net`, **not** `sites-available/default` (that vhost excludes the `ollama.` subdomain and returns 444 — cost real debugging time once).
  - **One dead backend used to garble every multi-sentence reply** (found live 2026-07-29, feiergente01 powered off). A failed chunk aborted `speakPiper`, and the handler's espeak fallback then re-spoke the **entire** text — heard as Kokoro delivering sentence one and espeak restarting the reply from the top, on 2 of 4 turns. Only long replies were affected: a single-chunk reply never reaches the odd index that goes to the second backend, so the failure looked intermittent and voice-related when it was purely a length threshold. `synthOne` now retries the chunk on the primary and benches the failed backend for `backend2Cooldown` (2 min) — a 502 costs ~2s, so probing a dead backend once per odd chunk taxes the whole outage. `writePCM` takes each chunk's **own** rate rather than chunk 0's, because a failover can return 22050Hz Piper where 24kHz Kokoro was expected and `aplay` is opened once for the reply.
  - **Voice names are mapped Python-side, before the daemon** (`zeev.py:8526`): `daniel`→`am_adam`, `sarina`→`af_heart`. Sending a persona name straight through is not harmless and the two backends disagree about it (**found by probing the endpoints directly, not from a device turn** — the mapping means production never sends the raw name, so this is a latent trap, not an active bug): bosgame's `tts_server.py` catches the Kokoro error and **silently degrades to English Piper (lessac), returning 200**, while feiergente01 returns 500. So an unmapped voice yields the wrong voice on one machine and a hard failure on the other, with no obvious error on either. `grep 'kokoro failed' ` in bosgame's `piper-tts` journal is the tell.
  - **feiergente01 setup**: `C:\kokoro\tts_server.py`, Windows service `ZeevTTS` via NSSM (auto-restart), logs `C:\kokoro\service_*.log`, firewall TCP 5601 inbound.
    - **Not tied to any login** — `sc qc ZeevTTS` confirms `SERVICE_START_NAME: LocalSystem` and `START_TYPE: AUTO_START`, so it runs at the lock screen and survives logoff, a different user logging in, and fast user switching. What *does* take it down is the machine sleeping, so check power settings before suspecting the service. `DEPENDENCIES` is empty, so on a cold boot it starts before the network is necessarily up — brief failures right after a feiergente reboot are expected and covered by NSSM restart plus the Pi's 2-minute backend cooldown, not a fault. **Gotcha**: stale per-user `typing_extensions` can shadow the global copy for the SYSTEM service — fix with `pip install --target=C:\Python314\Lib\site-packages --upgrade <pkg>`.
- **Voice personas**: Zeev's brain voice = Groq Orpheus `daniel`; device mode speaker = Kokoro `af_heart` ("Sarina", Zeev's secretary).

## User

Name: **Alex** (Linux username: `ragnar`). Always address as Alex in greetings and TTS prompts.
