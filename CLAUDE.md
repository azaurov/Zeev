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

**systemd**: `zeev-audio.service`, `User=ragnar`, `Restart=on-failure`.

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
- Always declare globals (e.g. `_SETTINGS_TTS_ON`) before use to avoid `SyntaxError`s.
- **`_gtts_fetch_chunk`** uses `urllib.request` (not `requests`) to avoid shared urllib3 connection pool conflicts with the bosgame fallback stream.

## Bluetooth

- `_BT_AUDIO_DEV` — active BlueALSA device string (`bluealsa:DEV=XX:XX,PROFILE=a2dp`).
- `_BT_RATE` / `_BT_CHANNELS` — negotiated A2DP format, queried from `bluealsa-aplay --list-pcms`.
- `bt_detect_connected()` — device mode startup; sets globals; retries 2× with 1s sleep.
- `bt_verify_connected()` — called at top of every `_speak_device()` call; clears `_BT_AUDIO_DEV` if device no longer listed (handles physical disconnects).
- `bt_scan()` — 10s scan via `subprocess.run(['timeout', N, 'bluetoothctl', 'scan', 'on'])`, parses all output after completion (avoids readline hang on partial ANSI escape sequences from bluetoothctl).
- Startup BT volume: raw 50/127 (~39%) via `amixer -D bluealsa cset numid=2 50`.
- `/bt` slash command: `scan`, `pair <N>`, `<N>` to connect, `off` to disconnect.

## Phone Calls (HFP)

- **SCO audio device**: `bluealsa:DEV=<mac>,PROFILE=sco,SRV=org.bluealsa` (BlueALSA v4.3.1+).
- **`bt_speak_sco` TTS chain**: Groq Orpheus → Cartesia (`sonic-2`, `CARTESIA_API_KEY`/`CARTESIA_VOICE_ID` in `.env`) → Piper (via daemon `speak_sco` or Python subprocess) → gTTS. All resampled via ffmpeg to SCO rate.
- **`bt_call_loop`**: turn 0 uses `bt_fast_detect` (records ≤6s, early-exits at ~3s on speech onset). **Live-person check runs before voicemail regex**: early onset + short transcript (≤5 words, onset >100ms) → `live` immediately — prevents Whisper hallucinations on 8kHz SCO audio from misclassifying a real pickup as voicemail.
- **`_speech_onset_ms`** — ms offset of first speech-energy frame (RMS > 400). Onset at 0ms = pickup click, not speech.
- **Speculative pre-generation**: background thread starts immediately after dialing to pre-generate voicemail message via LLM while ringing. Result in `_pregen_msg`. Cuts post-beep latency from ~14s to ~5s.
- **Call type detection**: voicemail (regex) → leave message + hangup; IVR (menu prompt regex) → DTMF digits; live/unknown → conversation. IVR hangup detection skipped ("Goodbye" is part of menu).
- **HFP guard**: `bt_hfp_detect()` called after post-dial sleep; if empty, hang up instead of entering `bt_call_loop` with invalid SCO device (previously caused a hard hang).
- **Whisper hallucination filter**: known hallucinations on ring tone ("Thank you.", "thanks", etc.) filtered before incrementing `turn`.
- **`groq_stt_call`** — Whisper with `_CALL_WHISPER_PROMPT` biasing toward call vocabulary on 8kHz audio.
- **`zeev/quantum_convo.py`** — quantum-weighted conversation topics for calls. Usage: `python3 zeev/quantum_convo.py --name NAME [--about TOPIC] [--call NUMBER]`.
- **Call-intent detection**: `_bt_call_match()` (not raw `_BT_CALL_RE.search()`) gates both call sites (device mode and terminal REPL) — it requires the call/dial/phone/ring trigger to appear within the first 15 chars of the transcript, so incidental mentions mid-sentence ("...my nieces call me Uncle Sasha...") aren't misread as a dial command.

## Version Control

Never commit data files (e.g. `adult_jokes.json`, imported corpora) unless explicitly asked. Add generated/data files to `.gitignore` by default.

## Shell Scripts / Deployment

Watch for CRLF line endings from copy-paste in shell scripts/sudoers; prefer inline commands or strip CRLF explicitly.

- **Deployment workflow**: Before running `./deploy.sh`, ALWAYS commit any local changes. The deploy script relies on `git push origin main`, so uncommitted changes will not be deployed.

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
- **`_groq_post`** — per-model 429 cooldown: `_groq_model_rate_limited_until` dict (model_id → epoch, 5-min backoff) so a 70B rate limit doesn't block 8B calls. Torah queries and 70B/R1 use 1200 max_tokens; 8B uses 600.
- **`_groq_post_with_fallback`** — wraps `_groq_post`; on 429/cooldown, retries once via OpenRouter free tier (`_OPENROUTER_FALLBACK_MODEL` maps each Groq model id → its OpenRouter equivalent, default `meta-llama/llama-3.3-70b-instruct:free`). Used by device-mode chat, web `/chat` SSE, thermal SSE, and detail prefetch — not by vision calls (no equivalent free vision model) or the 413 trim-retry loop. `_llm_post`'s own streaming path has a separate, longer OpenRouter→Gemini→bosgame chain and doesn't use this wrapper.
- **`_bosgame_stream`** — uses `http.client.HTTPSConnection` directly (not `requests`) to avoid urllib3 pool conflicts. Connect timeout 10s; socket timeout extended to 300s after connect for slow CPU inference.
- **`_llm_post`** — on connection errors, prints `[offline]` and falls back to `_bosgame_stream()`. Returns `(resp, err, provider)`.
- **`extract_memory`** — prefers `_bosgame_complete` (llama3.2:1b, ~5–10s) over Groq; falls back on error.
- **`route_model`** — `_REASONING_RE` → DeepSeek R1; `_SMART_RE` → 70B; default → 8B. No extra API call.
- **`zeev_cleanup()`** — kills `_MUSIC_PROC` and `_piper_term_proc`; `pkill -f` for `zeev_music`, `zeev_rec.wav`, `piper --model`, `mpg123`; removes `/tmp/zeev_*`. Registered via `atexit` in `main()` and `run_device_mode()` so it also fires on unhandled exceptions (not just SIGINT/SIGTERM).
- **`run_web_server`** — `ThreadingHTTPServer`. Endpoints: `/chat` (SSE stream), `/clear`, `/memory`, `/memorize`, `/tts` (POST text → WAV), `/transcribe` (raw audio → transcript), `/thermal` (SSE), `/thermal-status`, `/volume` (GET/POST), `/snap`, `/gps`.
- **`_build_system_prompt`** — assembles: base persona + memory facts + RAG hits + optional calendar + optional Tavily results. `needs_weather(text)` (subset of `needs_search`, matches "weather"/"forecast"/etc.) appends a units instruction telling the model to spell out `°F`→"degrees Fahrenheit" and `mph`→"miles per hour" in full words, since replies are spoken via TTS.
- **SQLite** (`data/zeev.db`, WAL mode): tables `messages`, `facts`, `notes`, `settings`, `quantum_insights`. `_db_lock` guards all writes (thread-safety for ThreadingHTTPServer).

### Models (Groq)

| Key | Model ID | Use |
|---|---|---|
| `1` | `llama-3.1-8b-instant` | Fast — casual chat |
| `2` | `llama-3.3-70b-versatile` | Smart — code, writing |
| `3` | `deepseek-r1-distill-llama-70b` | Reasoning — math, logic |

### Key constants

| Constant | Value |
|---|---|
| `PRIOR_TURNS` | 15 turns loaded from DB per session |
| `max_tokens` | 600 (8B) / 1200 (70B, R1, Torah) |
| `temperature` | 0.75 |
| `VISION_MODEL` | `meta-llama/llama-4-scout-17b-16e-instruct` |

### Google Calendar

`gcal_fetch(days=1)` reads `data/gcal_token.json` (OAuth2, from `gcal_auth.py`), auto-refreshes token, fetches events, cached 5 min. `needs_calendar(text)` triggers on "calendar", "schedule", "meeting", etc. — silently skips if token absent. `gcal_days_from_query` maps "tomorrow"→2, "this week"→7, "next week"→14, "this month"→30. Injects as `## Today's calendar:` or `## Calendar (next N days):`.

### GPS / geolocation

Tiered pipeline: WiFi AP triangulation (Google Geolocation API → beacondb) → IP fallback (`ip-api.com`). `gps_locate()` cached 30 min. `_reverse_geocode` via Nominatim/OSM. `GOOGLE_GEOLOC_KEY` in `.env`. `/gps` terminal command; `GET /gps` web endpoint.

### User memory

`facts` table in `zeev.db`. Injected into every system prompt under `## What I know about Alex:`. `extract_memory()` runs on `quit` or `/memorize`. 429 rate-limit returns `None` — caller shows warning instead of fake success. `/forget-fact N` to remove.

### History RAG

`build_rag_index()` — last 500 `messages` rows, inverted word index, stop words filtered. `retrieve_relevant(query, k=2, min_score=2)` — injects as `## Relevant past exchanges:`. Called by `init_learning()` at startup.

### Torah RAG (Sefaria)

FTS5 DB: `data/torah.db`. Sources: Tanakh, Mishna, Talmud, Apocrypha, Siddur/Haggadah, Zohar, Dead Sea Scrolls, Sumerian. Populated by `zeev/import_sefaria.py` (resume-safe, ~75 min full corpus).

- `needs_torah(text)` / `_TORAH_RE` — matches Torah, Talmud, Gemara, halacha, Apocrypha, liturgy, Zohar, DSS/Qumran, Sumerian, parsha/portion.
- `torah_search(query, k=3)` — FTS5 search; noise verbs ("recite", "tell", "say") and time words excluded from FTS to avoid polluting passage matching.
- DB schema: `passages` FTS5 table with `source`, `ref`, `en`, `he` columns. `done` table tracks imported refs.
- Torah/Sefaria replies force `lang='he'` for TTS regardless of reply script.

### Weekly reflection

`zeev/weekly_reflection.py` — synthesizes the last 7 days of messages into a first-person Zeev reflection (themes, patterns, open questions, emotional context). Stored in `reflections` table; loaded by `init_learning()` and injected into every system prompt under `## Weekly reflection:` (capped at 1500 chars). LLM: bosgame `llama3.1:8b` first, Groq 70B fallback. Cron: `0 7 * * 0` (Sunday 7 AM). `--show` prints latest; `--days N` adjusts window. Skips if fewer than 10 messages in the window.

### Quantum reasoning

`quantum_reason(idea, llm_fn, past_insights=None)` in `zeev/quantum.py`: idea → circuit spec → simulate → interpret. `past_insights` (k=3 most recent from `quantum_insights` table) compound learning over time.

`zeev/quantum_daily.py` — 8 human-dilemma scenarios, one per day (`day-of-year % 8`). Cron: `0 6 * * *`.

### Music playback

`youtube_play(query, adev=None)` — `yt-dlp --default-search ytsearch1` → ffmpeg → mpg123. `play <query>` / `stop the music` detected before LLM routing. `/stop` kills playback.

### Multilingual TTS

| Language | Detection | Terminal/device | Web UI |
|---|---|---|---|
| English | default (always) | Piper `en_US-lessac-medium` | Groq Orpheus `daniel` |
| Hebrew | `/lang he` (terminal/web) or voice (device) | gTTS + mpg123 | gTTS MP3 → `speechSynthesis` `he-IL` |
| Spanish | `/lang es` (terminal/web) or voice (device) | gTTS + mpg123 | gTTS MP3 → `speechSynthesis` `es-MX` |
| Russian | `/lang ru` (terminal/web) or voice (device) | **device**: local Piper `ru_RU-irina-medium` (female) tried first via `_piper_direct()`, gTTS fallback on failure; **terminal**: gTTS + mpg123 | gTTS MP3 → `speechSynthesis` `ru-RU` |

`detect_lang` no longer auto-detects from character sets — always returns `FORCED_LANG or "en"`. Terminal/web change language via `/lang` command only — device mode (`run_device_mode()`) has no `/lang` handler, so it instead uses `lang_switch_intent()` (`zeev.py:720`): a voice trigger requiring a speak/talk/switch/say verb within 20 chars of the language name (guards against incidental mentions like "I have a Russian friend", mirroring `_bt_call_match`'s proximity check). Wired into `_handle_transcript()` right after the voice-coach intent block; sets `FORCED_LANG` and speaks a confirmation in the target language. Groq Orpheus is English-only. `/tts` endpoint tries gTTS before returning `503 {"lang": ...}` for browser `speechSynthesis` fallback.

**Device-mode Piper is not daemon-routed for non-English**: the Go audio daemon (`zeev-audio/internal/server/handlers.go`) only speaks Piper for `req.Lang in ("", "en")`, falling back to espeak-ng for anything else — so `_audio.speak_sync(lang="ru")` would silently produce a robot voice, not the local Piper Russian model. `_speak_device()` therefore has a step 0 that calls `_piper_direct()` (bypasses the daemon, invokes `PIPER_BIN` directly) for Russian before reaching the ElevenLabs/gTTS step. `_piper_direct()` is also reused by the generic Piper fallback (step 2) — extracted to avoid duplicating the BT-one-shot-vs-persistent-process/ffmpeg-resample/aplay logic. `PIPER_MODELS["ru"]` prefers `ru_RU-irina-medium.onnx` (female) over `ru_RU-dmitri-medium.onnx` if both are present (`init_tts()`).

**Russian Piper latency**: real-time factor is ~2.6x on the Pi Zero 2W (confirmed via direct CLI timing) — no "low" quality tier exists for any `ru_RU` Piper voice (checked all 4 on the `rhasspy/piper-voices` HF repo: denis/dmitri/irina/ruslan are medium-only), so speed can't be traded for a smaller model. Step 0 instead chunks the reply via `_gtts_chunks()` and calls `_piper_direct()` per sentence so playback starts after the first sentence's inference (~7-8s) rather than the whole reply's (measured 16.8s→7.7s time-to-first-audio on a 3-sentence reply, one-shot-process benchmark — production reuses a persistent process across chunks so real numbers are better). Total synthesis time is roughly unchanged by chunking, so `_PIPER_RU_MAX_WORDS` (35) skips Piper for longer replies and goes straight to gTTS. If a chunk fails mid-reply, no gTTS fallback fires (would re-speak already-played sentences) — only the first chunk failing triggers the gTTS fallback.

**LLM must output actual Cyrillic, not transliteration**: the 8B model reliably romanizes Russian ("Zdravstvuy, Sashen'ka...") and adds parenthetical English glosses even when told "Reply in Russian only" — Piper's Cyrillic phonemizer mispronounces Latin-letter input, so this isn't a synthesis bug, it's a prompt-compliance one. `_LANG_INSTRUCTIONS["ru"]`/`["he"]` (`zeev.py:4364`) explicitly forbid transliteration and English parentheticals, with a concrete before/after example for Russian — verified against the live Groq API across several prompts before landing. If garbled/wrong-language audio recurs, check the raw LLM reply text first (`journalctl -u zeev-device | grep 'Zeev \[8B\]'`) before assuming a TTS-layer bug.

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
zeev/
  zeev.py                   # entire application
  audio_client.py           # Python adapter for zeev-audio daemon
  mlx90640.py               # MLX90640 thermal camera (I2C bus 3)
  import_sefaria.py         # populate data/torah.db
  migrate_to_sqlite.py      # idempotent flat-file → zeev.db import
  quantum_daily.py          # daily quantum teaching (cron 0 6 * * *)
  quantum_convo.py          # quantum-weighted call topics
  data/                     # runtime files (git-ignored)
    zeev.db                 # WAL SQLite: messages, facts, notes, settings, quantum_insights, reflections
    torah.db                # FTS5 corpus (Tanakh/Mishna/Gemara/Zohar/DSS/Sumerian/…)
zeev-audio/                 # Go audio daemon (cross-compiled arm64)
  cmd/zeev-audio/main.go
  internal/piper/piper.go   # persistent Piper process + SynthesizeOneShot
  internal/audio/           # alsa.go, keepalive.go
  internal/bt/              # detect.go, scan.go, connect.go
  internal/music/music.go
  internal/record/record.go # arecord + RMS VAD
  internal/server/          # server.go (Unix socket), handlers.go (cmd dispatch)
  Makefile                  # make pi → cross-compile arm64
~/zeev-audio/zeev-audio     # binary on Pi (outside repo)
~/piper/                    # Piper TTS (outside repo); en_US-lessac-medium.onnx
swiftkey_system_prompt_snippet.md  # personal vocabulary appended to system prompt
```

### Thermal camera (MLX90640)

Software I2C bus 3 on GPIO5 (SDA) / GPIO6 (SCL). Config in `/boot/firmware/config.txt`:
```
dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=5,i2c_gpio_scl=6,i2c_gpio_delay_us=10
```
Address `0x33` on `/dev/i2c-3`. Hardware I2C `/dev/i2c-1` is used by WM8960 at `0x19`. Logic in `zeev/mlx90640.py` uses `smbus2`-backed busio shim (Adafruit blinka can't route to bus 3 automatically).

### Whisplay HAT device mode

`python3 zeev/zeev.py --device`

- **TTS priority**: Groq Orpheus → gTTS + mpg123 (he/es/ru) → Piper (en, one-shot for BT, persistent for speaker) → espeak-ng
- **Speaker volume**: raw 113 (~89%) via amixer at startup. BT headphone volume: raw 50/127 (~39%).
- **STT**: Groq Whisper `whisper-large-v3-turbo`.
- **`_greeting_done` event**: gates the wake listener — `_wake_listener` blocks until greeting finishes so it can't self-trigger on the greeting audio through the mic.
- **429 fallback**: `_handle_transcript` retries 70B/R1 429s with 8B before surfacing an error.
- **LLM error display**: Whisplay screen shows "Rate limited", "No network", or "LLM err <code>". Full detail appended to `data/zeev_errors.log`.
- **`_CAMERA_RE`**: natural-language camera intents → `capture_image()` + Llama 4 Scout vision call (when `CAMERA_AVAILABLE`).
- **`_VISUAL_TRIGGER_RE`**: natural-language visual-effect intents ("show me fire", "do the matrix effect", "play a psychedelic light show") → runs the matching `shapes_test.py` effect (`fire`/`matrix`/`psychedelic`/`liquid`/`tunnel`/`plasma`/`cartoon`) directly on `board` for 12s. Sets `_visual_effect_active[0] = True` first so the background `_face_loop` thread skips its own SPI writes for the duration (avoids two threads racing on `board.draw_image`).
- Driver install: `cd ~/Whisplay && sudo bash install_driver.sh && sudo reboot`

## Startup / Shutdown

- `zeev_cleanup()` runs at startup in all modes (clear crash leftovers) and is registered via `atexit` in `main()` and `run_device_mode()` so hanging Piper/mpg123 subprocesses are killed on any exit including unhandled exceptions.
- `main()` startup greeting via gTTS+mpg123 (background, ~2s). Exit: "Goodbye, Alex." synchronously before `sys.exit()`.
- Journald persistent storage: `journalctl -b -1` works across reboots.

## bosgame Ollama integration

bosgame (LAN `10.0.0.141`) runs Ollama as free local inference backend.

- **Endpoint**: `https://ollama.sogdiana-gematria.net/ollama/` (grey-cloud DNS → direct LAN, bypasses Cloudflare)
- **Auth**: `X-Zeev-Key` header (`BOSGAME_KEY` in `.env`)
- **`.env` keys**: `BOSGAME_URL`, `BOSGAME_MODEL=llama3.2:1b`, `BOSGAME_KEY`
- **Models**: `llama3.1:8b` (chat fallback), `llama3.2:1b` (memory extraction)
- **Pi `/etc/hosts`**: `10.0.0.141 ollama.sogdiana-gematria.net` — **required** to avoid NAT hairpin. Persisted via `/etc/cloud/templates/hosts.debian.tmpl` (cloud-init resets `/etc/hosts` on reboot without this).
- **bosgame nginx**: edit `sites-enabled/default` (NOT `sites-available/default` — they are separate files on bosgame). `/ollama/` location: `proxy_buffering off`, `proxy_read_timeout 300s`.
- Fallback only works on home LAN.

## bosgame Kokoro TTS server

Primary English TTS: **Kokoro** on bosgame. Pi daemon calls `https://ollama.sogdiana-gematria.net/piper/tts`.

- **Server**: `~/piper/tts_server.py` (port 5600, localhost-only). Service: `piper-tts.service`.
- **Kokoro**: `~/kokoro/kokoro-v1.0.onnx` + `voices-v1.0.bin`. Default voice: `af_heart` (Sarina, 24kHz, speed=1.0). Set via `KOKORO_VOICE` env var or per-request `"voice"` field. Latency: ~1-2s.
- **Piper fallback**: `~/piper/en_US-lessac-medium.onnx` (22050Hz). Latency: ~0.7s.
- **Go daemon** (`REMOTE_PIPER_URL` env var): parses WAV header bytes 24-27 for sample rate. `REMOTE_PIPER_VOICE` sets default voice; falls back to `BOSGAME_KEY` if `REMOTE_PIPER_KEY` is unset. Per-request `"voice"` field overrides the default for that call.
- **Voice personas**: Zeev's brain voice = Groq Orpheus `daniel`. Device mode speaker = Kokoro `af_heart` ("Sarina", Zeev's secretary).

## User

Name: **Alex** (Linux username: `ragnar`). Always address as Alex in greetings and TTS prompts.
