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
- **`_groq_post`** — per-model 429 cooldown: `_groq_model_rate_limited_until` dict (model_id → epoch, 5-min backoff) so a 70B limit doesn't block 8B calls. Torah/70B/R1 use 1200 max_tokens; 8B uses 600.
- **`_groq_post_with_fallback`** — wraps `_groq_post`; on 429/cooldown, retries once via OpenRouter free tier (`_OPENROUTER_FALLBACK_MODEL` maps each Groq model id → its equivalent, default `meta-llama/llama-3.3-70b-instruct:free`). Used by device-mode chat, web `/chat` SSE, thermal SSE, detail prefetch — not vision (no free equivalent) or the 413 trim-retry loop. `_llm_post`'s streaming path has its own longer OpenRouter→Gemini→bosgame chain.
- **`_bosgame_stream`** — uses `http.client.HTTPSConnection` directly (not `requests`) to avoid urllib3 pool conflicts. Connect timeout 10s; socket timeout extends to 300s after connect for slow CPU inference.
- **`_llm_post`** — on connection errors, prints `[offline]` and falls back to `_bosgame_stream()`. Returns `(resp, err, provider)`.
- **`extract_memory`** — prefers `_bosgame_complete` (llama3.2:1b, ~5–10s) over Groq; falls back on error.
- **`route_model`** — `_REASONING_RE` → DeepSeek R1; `_SMART_RE` → 70B; default → 8B. No extra API call.
- **`zeev_cleanup()`** — kills `_MUSIC_PROC`/`_piper_term_proc`, `pkill -f` for `zeev_music`/`zeev_rec.wav`/`piper --model`/`mpg123`, removes `/tmp/zeev_*`. Registered via `atexit` so it fires on unhandled exceptions too, not just SIGINT/SIGTERM.
- **`run_web_server`** — `ThreadingHTTPServer`. Endpoints: `/chat` (SSE stream), `/clear`, `/memory`, `/memorize`, `/tts` (POST text → WAV), `/transcribe` (raw audio → transcript), `/thermal` (SSE), `/thermal-status`, `/volume` (GET/POST), `/snap`, `/gps`.
- **`_build_system_prompt`** — assembles base persona + memory facts + RAG hits + optional calendar + optional Tavily results. `needs_weather(text)` (subset of `needs_search`) appends a units instruction to spell out `°F`→"degrees Fahrenheit" / `mph`→"miles per hour" in full words, since replies are spoken via TTS.
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

`gcal_fetch(days=1)` reads `data/gcal_token.json` (OAuth2), auto-refreshes token, cached 5 min. `needs_calendar(text)` triggers on "calendar"/"schedule"/"meeting" — silently skips if token absent. `gcal_days_from_query` maps "tomorrow"→2, "this week"→7, "next week"→14, "this month"→30.

### GPS / geolocation

Tiered pipeline: WiFi AP triangulation (Google Geolocation API → beacondb) → IP fallback (`ip-api.com`). `gps_locate()` cached 30 min. `_reverse_geocode` via Nominatim/OSM. `/gps` terminal command; `GET /gps` web endpoint.

### User memory

`facts` table in `zeev.db`, injected under `## What I know about Alex:`. `extract_memory()` runs on `quit`/`/memorize`. 429 returns `None` — shows a warning instead of fake success. `/forget-fact N` to remove.

### History RAG

Two layers. `_build_system_prompt` calls `retrieve_semantic(...) or retrieve_relevant(...)` — semantic first, keyword as the offline fallback — and injects the winner as `## Relevant past exchanges:`.

- **Semantic (preferred)**: `embed_text()` → bosgame `nomic-embed-text` via the existing `/ollama` proxy (`{BOSGAME_URL}/api/embeddings`, `X-Zeev-Key`). Vectors cached in the `message_vecs` table (`message_id`, `dim`, `vec` BLOB of little-endian f32), so retrieval is a local dot product — only the query embedding is a network call. Embedding runs remotely for the same reason Russian TTS does: the Pi has one usable core and ~200 MB headroom. Measured backfill rate: **~1.4 msg/s**. `EMBED_MODEL` overrides the model; vectors whose `dim` doesn't match the query are skipped, so switching models degrades instead of corrupting.
- **Keyword (fallback)**: `build_rag_index()` — last 500 rows, inverted index, stop words filtered; `retrieve_relevant(query, k=2, min_score=2)`. Its `_tokenize` is `\b[a-z]{3,}` — **ASCII-only, so Hebrew/Russian/Spanish history was never indexed at all**, and it can't see past 500 messages. Both are why the semantic layer exists. Kept because `embed_text()` returning `None` is a normal state for a travelling Pi.
- **Language guard**: because semantic recall can now surface Hebrew/Cyrillic into an English turn's prompt, `_build_system_prompt` appends an explicit "reply in English regardless" note when a hit contains those scripts and no `FORCED_LANG` is set. The 8B model flips script easily — see the transliteration notes above.
- **`_memory_maintenance_loop()`** (started by `init_learning()`): every 30 min, backfills up to 50 new message vectors and re-runs `load_latest_reflection()`. Both were startup-only before, so a device up for weeks never saw a new weekly reflection and post-boot messages were never embedded.
- **`save_memory()` is transactional** (`BEGIN IMMEDIATE` + rollback). It previously did `DELETE FROM facts` then re-INSERT outside a transaction — an exception or power cut between the two lost every fact. Pinned by `tests/test_semantic_memory.py`.

### Torah RAG (Sefaria)

FTS5 DB: `data/torah.db`. Sources: Tanakh, Mishna, Talmud, Apocrypha, Siddur/Haggadah, Zohar, Dead Sea Scrolls, Sumerian. Populated by `zeev/import_sefaria.py` (resume-safe, ~75 min full corpus).

- `needs_torah(text)` / `_TORAH_RE` — matches Torah, Talmud, Gemara, halacha, Apocrypha, liturgy, Zohar, DSS/Qumran, Sumerian, parsha/portion. `torah_search(query, k=3)` — FTS5 search; noise verbs/time words excluded to avoid polluting matching.
- DB schema: `passages` FTS5 table (`source`, `ref`, `en`, `he`); `done` tracks imported refs. Torah/Sefaria replies force `lang='he'` for TTS regardless of reply script.

### Weekly reflection

`zeev/weekly_reflection.py` — synthesizes the last 7 days of messages into a first-person Zeev reflection. Stored in `reflections` table; injected into every system prompt under `## Weekly reflection:` (capped 1500 chars). LLM: bosgame `llama3.1:8b` first, Groq 70B fallback. Cron: `0 7 * * 0`. Skips if fewer than 10 messages in the window.

### Quantum reasoning

`quantum_reason(idea, llm_fn, past_insights=None)` in `zeev/quantum.py`: idea → circuit spec → simulate → interpret. `past_insights` (k=3 most recent) compound learning over time. `quantum_daily.py` — 8 human-dilemma scenarios, one per day. Cron: `0 6 * * *`.

### Music playback

`youtube_play(query, adev=None)` — `yt-dlp --default-search ytsearch1` → ffmpeg → mpg123. `play <query>`/`stop the music` detected before LLM routing.

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
zeev/data/                   # runtime (git-ignored): zeev.db, torah.db
zeev-audio/                  # Go audio daemon (cross-compiled arm64)
  cmd/zeev-audio/main.go, internal/{piper,audio,bt,music,record,server,sdnotify}/
  Makefile                  # make pi → cross-compile arm64
~/zeev-audio/zeev-audio     # binary on Pi (outside repo)
~/piper/                    # Piper TTS (outside repo); en_US-lessac-medium.onnx
```

### Thermal camera (MLX90640)

Software I2C bus 3 on GPIO5 (SDA) / GPIO6 (SCL): `dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=5,i2c_gpio_scl=6,i2c_gpio_delay_us=10` in `/boot/firmware/config.txt`. Address `0x33` on `/dev/i2c-3`. Hardware I2C `/dev/i2c-1` is used by WM8960 at `0x19`. `zeev/mlx90640.py` uses a `smbus2`-backed busio shim (Adafruit blinka can't route to bus 3 automatically).

### Whisplay HAT device mode

`python3 zeev/zeev.py --device`

- **TTS priority**: Groq Orpheus → gTTS + mpg123 (he/es/ru) → Piper (en, one-shot for BT, persistent for speaker) → espeak-ng
- **Streaming replies**: `_stream_speak()` flushes complete sentences to TTS as the LLM generates them, so time-to-first-audio is one sentence rather than one completion. Batching is greedy to the last terminator with a 45-char floor. Tokens are drained by a producer thread — `_speak_device` blocks for the duration of the audio, and the 1600-token Torah path would otherwise leave the HTTP response unread long enough to be dropped. `STREAM_TTS=0` reverts to the buffered path. The 429→8B and 413→trim chains are unaffected (`status_code` is available before the body is read). Chunking rule pinned by `tests/test_stream_chunking.py`; the tail rule is subtle — a dangling fragment is dropped, but a reply with *no* terminator is still spoken, distinguished by whether anything was spoken yet.
- **Interrupting speech (three separate bugs, all fixed together)**: the button did nothing once the Go daemon owned playback. `_interrupt_tts` only killed `_tts_p1`/`_tts_p2`, which are `None` on the daemon path.
  1. Daemon `stop` only stops **music**. Speech needed its own `speak_stop` command (`audio.CancelSpeech()` + `audio.StopPlayback()`).
  2. Killing `aplay` is not enough: `speakPiper` feeds one `APlayPipe` from a loop that **blocks on `<-results[i]`** waiting for later sentences to synthesize, so a cancel mid-synthesis had nothing to kill and the loop kept waiting. Hence the `context` scope (`BeginSpeech`/`CancelSpeech`) and the `select` on `speechCtx.Done()`.
  3. Cancelling made `speakPiper` return an error, and the handler's fallback then **re-spoke the whole reply through espeak-ng** — which looked exactly like the cancel not working. `audio.ErrSpeechCancelled` is now treated as success, never as a synthesis failure.
  - Python side: `audio_client.speak_stop()` opens its **own short-lived socket**. Every other call serializes behind `self._lock` on one socket, so a stop sent during `speak_sync` would queue behind the very speech it was cancelling.
  - `_on_press` now also interrupts during `thinking`, not just `speaking` — with streaming, state stays `thinking` until the first sentence flushes.
- **Speaker volume**: `_STARTUP_VOLUME` (default **90%**, raw 114/127) via amixer at startup; override with `ZEEV_VOLUME`. The speaker is inches from the mic with no echo cancellation, so louder settings feed more of Zeev's own voice into the wake detector — turn this down first if false wakes reappear. `_VOLUME` is initialized from the same constant so `/volume` doesn't report a stale value. BT headphone volume: raw 50/127 (~39%).
- **STT**: Groq Whisper `whisper-large-v3-turbo`.
- **`_greeting_done` event**: gates the wake listener so it can't self-trigger on the greeting audio through the mic.
- **Wake word ("Miss Minutes")**: `_wake_listener()` picks one of two front-ends, both feeding the shared `_wake_dispatch()` (session claim → beep → dispatch or record follow-up), so the paths can't drift on state handling.
  - **openWakeWord (preferred)**: one continuous 16kHz raw `arecord` stream, scored on-device per 1280-sample (80ms) frame. Enable with `OWW_MODEL_PATH` in `.env`; `OWW_THRESHOLD` (0.5) and `OWW_COOLDOWN` (2.0s) tune it. Install `onnxruntime` + `openwakeword`.
    - **Measured on the Pi Zero 2W**: RTF 0.68, **~70% of one core** (of four), **166 MB steady RSS**, 4.8s model load. Coexists with the rest of zeev-device at ~257 MB of 463. Not cheap — it only runs in `idle`/`ready` and releases the mic and the core the moment a turn starts.
    - **pip resolves 0.4.0, not 0.6.x, on py3.13/aarch64** — newer releases depend on `tflite-runtime`, which has no wheel there. 0.4.0 is onnx-only and bundles its models (`resources/models/*.onnx`, incl. `hey_jarvis_v0.1.onnx`). Don't blind-upgrade it. Note the API differs from current docs: `Model(wakeword_model_paths=[...])`, and there is no `download_models`.
    - **`vad_threshold` is not a saving** — in 0.4.0 Silero runs *in addition to* the main model, measured 75.6% vs 65.6% of a core. Left off deliberately.
    - **Custom phrases**: train a `.onnx` with openWakeWord's automatic training notebook (free, ~75–90 min on a Colab T4). Verified that a model trained on the modern pipeline loads and runs under the 0.4.0 runtime, so trained models drop straight in.
    - **`model.reset()` is mandatory, not hygiene (caused an endless loop live)**: `predict()` scores a *rolling* buffer of embeddings. Killing `arecord` when a turn starts and restarting it afterwards does **not** clear that buffer, so the previous turn's wake phrase is still inside it and scores again on the first fresh frame. Symptom: every reply's `[+Ns] Done` followed in the same second by `trigger (score 1.00)` — **an identical score every time means stale buffer, not a real detection**. Zeev answered its own noise forever. `reset()` now runs on trigger and again before re-arming.
    - **`OWW_SETTLE` (1.5s)**: quiet period after a turn before the mic re-arms. The speaker is inches from the mic with no echo cancellation, and accepting `ready` re-arms instantly, so the tail of Zeev's own reply retriggers the wake word. Related: startup volume was dropped to 65% partly to weaken this feedback path.
    - **Follow-up transcripts are noise-guarded** with `re.search(r'\w{2,}')`, the same check `bt_call_loop` uses (`zeev.py:~2880`). A spurious wake records room noise and Whisper returns confident nonsense (`"A A"`, `"Ache, a full-tongue, a full-tongue"`) which otherwise becomes a real LLM turn and keeps a loop fed.
    - **Porcupine was evaluated and rejected**: far cheaper at runtime (0.7% of a core, measured) but custom keywords are Enterprise-only at $6k/yr — the free tier is a 7-day trial pending review. Don't re-propose it.
  - **Cloud fallback**: used automatically when `OWW_MODEL_PATH` is unset or the model won't load. Records 2s chunks, applies an RMS gate then `_has_speech()`, and only then pays for Groq Whisper and substring-matches `_DEVICE_WAKE_WORDS`. **This bills per noisy 2s window, continuously** — it is a fallback, not a resting state.
  - **Behavior difference**: the cloud path can catch "Miss Minutes, what's the weather" in one window and pass the tail straight through; the local path fires on the phrase alone, so the follow-up is always recorded separately. Speak, pause, then ask.
  - **`_has_speech()`** (`zeev.py`, near `_rms`): webrtcvad at **aggressiveness 3, deliberately not the library default** — measured on this Pi, levels 0–2 passed every synthetic signal except digital silence (which the RMS gate already rejects), making the gate a no-op. Level 3 also rejects pure tones and hiss; broadband noise and mains hum still pass. Fails open when webrtcvad is missing so it can never make the device deaf. Pinned by `tests/test_wake_gate.py`. On Python 3.13 the Pi needs the **`webrtcvad-wheels`** fork — the original imports `pkg_resources`, dropped in setuptools 82.
- **Wake-word state coupling (bit us once)**: the listener only runs when `_face_state` is `idle`/`ready`. `_busy` is cleared *only* in `_go_idle()`, and the success path calls `_go_ready()` — so before this was fixed, one voice turn locked the wake word out for the rest of the boot. `_idle_sleep_watcher` now also drops a quiet `ready` session back to `idle`. The listener deliberately does **not** gate on `_screen_on[0]`.
- **429 fallback**: `_handle_transcript` retries 70B/R1 429s with 8B before surfacing an error.
- **LLM error display**: Whisplay screen shows "Rate limited", "No network", or "LLM err <code>"; full detail in `data/zeev_errors.log`.
- **`_CAMERA_RE`**: natural-language camera intents → `capture_image()` + Llama 4 Scout vision call (when `CAMERA_AVAILABLE`).
- **`_VISUAL_TRIGGER_RE`**: visual-effect intents → runs the matching `shapes_test.py` effect (`fire`/`matrix`/`psychedelic`/`liquid`/`tunnel`/`plasma`/`cartoon`) on `board` for 12s; sets `_visual_effect_active[0] = True` first so `_face_loop` skips its own SPI writes (avoids two threads racing on `board.draw_image`).
- **`_push_lcd` / RGB565**: every frame is hand-converted to RGB565. This was a pure-Python loop over 240×280 = 67,200 pixels measuring **380 ms/frame on the Pi**, against a `_FACE_INTERVAL` asking for up to 8 fps (125 ms) — so the face loop could never hit its rate and simply pinned a core for the whole session. The numpy path (`_rgb565_np`) is **4.6 ms, an 83× speedup**; `_rgb565_py` remains as a fallback so a missing numpy degrades the frame rate instead of blanking the screen. Byte-equivalence is pinned by `tests/test_rgb565.py` — `>u2` (big-endian) byte order matters. If the display ever shows wrong colours or tearing, check that test first.
- Driver install: `cd ~/Whisplay && sudo bash install_driver.sh && sudo reboot`.

## Startup / Shutdown

- `zeev_cleanup()` runs at startup in all modes (clear crash leftovers) and is registered via `atexit` in `main()` and `run_device_mode()` so hanging Piper/mpg123 subprocesses are killed on any exit including unhandled exceptions.
- `main()` startup greeting via gTTS+mpg123 (background, ~2s). Exit: "Goodbye, Alex." synchronously before `sys.exit()`.
- Journald persistent storage: `journalctl -b -1` works across reboots.

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
- **Second backend (feiergente01)**: `REMOTE_PIPER_URL2`/`REMOTE_PIPER_KEY2` → second Kokoro instance on `feiergente01` (Windows 11, i7-1360P, no GPU, LAN `10.0.0.208:5601`, RTF ~0.68). `speakPiper` alternates sentence chunks between backends (bosgame even, feiergente01 odd) — separate CPUs avoid same-backend contention (single-threaded `tts_server.py`). Cuts a 99-word/5-sentence reply's overhead from ~10s+ to ~3-4s.
  - **Public exposure**: `REMOTE_PIPER_URL2=https://ollama.sogdiana-gematria.net/piper2/tts`, key = `BOSGAME_KEY` (nginx rewrites to feiergente01's real key). Location is in `/etc/nginx/sites-available/ollama.sogdiana-gematria.net`, **not** `sites-available/default` (that vhost excludes the `ollama.` subdomain and returns 444 — cost real debugging time once).
  - **feiergente01 setup**: `C:\kokoro\tts_server.py`, Windows service `ZeevTTS` via NSSM (auto-restart), logs `C:\kokoro\service_*.log`, firewall TCP 5601 inbound. **Gotcha**: stale per-user `typing_extensions` can shadow the global copy for the SYSTEM service — fix with `pip install --target=C:\Python314\Lib\site-packages --upgrade <pkg>`.
- **Voice personas**: Zeev's brain voice = Groq Orpheus `daniel`; device mode speaker = Kokoro `af_heart` ("Sarina", Zeev's secretary).

## User

Name: **Alex** (Linux username: `ragnar`). Always address as Alex in greetings and TTS prompts.
