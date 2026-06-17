# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Hardware Environment

This is a Raspberry Pi project (Zeev) using Python with hardware HATs (Whisplay display, PiSugar battery, WM8960 audio). Always verify hardware-related config changes (`config.txt`, I2C/SPI baudrates) before assuming software causes for audio/display failures.

## TTS / Audio

TTS uses Piper with a persistent process for the wired speaker path; do NOT use per-sentence model reloads or short inter-chunk timeouts (the 0.3s timeout caused only the first sentence to play). Always declare globals (e.g. `_SETTINGS_TTS_ON`) before use to avoid `SyntaxError`s.

In device mode, `_speak_device()` retries Piper once on `BrokenPipeError`/`OSError` or empty audio by resetting `_piper_dev_proc = None` and restarting the process — only falls through to espeak-ng if both attempts fail.

**BT vs speaker Piper path**: when BT headphones are connected, `_speak_device()` uses a **one-shot** Piper subprocess (close stdin after writing, read stdout until EOF). This guarantees the full multi-sentence response is captured before playback, avoiding the timeout-truncation problem on the Pi Zero 2W (~2s synthesis per sentence). The persistent process path is only used for the wired speaker.

**BT audio resampling**: all audio going to BlueALSA must match the negotiated A2DP format (`_BT_RATE` / `_BT_CHANNELS`, queried from `bluealsa-aplay --list-pcms`). Both Orpheus (24000Hz mono WAV) and Piper (22050Hz mono raw PCM) are resampled via an inline `ffmpeg` pipeline before `aplay`. Do NOT use `plug:bluealsa:...` ALSA syntax — the parser cannot handle colons in nested params; pass explicit `-f S16_LE -r _BT_RATE -c _BT_CHANNELS` flags to `aplay` instead.

`_collect_piper_audio(p, first_timeout=30.0, idle_timeout=2.0)` — reads from the Piper subprocess with a 30s first-chunk timeout (covers cold ONNX model load on Pi Zero 2W, ~20s) then 2s idle timeout between chunks.

WM8960 auto-powers-down after ~30s of ALSA inactivity. Device mode runs a keepalive thread that plays a 1s silent buffer every 20s to prevent this.

All audio output (TTS, music) routes through `bt_audio_dev()` which returns the active BlueALSA PCM string when Bluetooth headphones are connected, or `plughw:wm8960soundcard,0` otherwise. Requires `bluez-alsa-utils` + `libasound2-plugin-bluez` on the Pi.

## Bluetooth

- `_BT_AUDIO_DEV` global holds the active BlueALSA device string (`bluealsa:DEV=XX:XX,PROFILE=a2dp`) when headphones are connected.
- `_BT_RATE` / `_BT_CHANNELS` globals — store the negotiated A2DP format (e.g. 44100Hz, 1ch), queried from `bluealsa-aplay --list-pcms` at connect/startup.
- `bt_detect_connected()` — called at device mode startup; queries `bluealsa-aplay --list-pcms`, sets `_BT_AUDIO_DEV`/`_BT_RATE`/`_BT_CHANNELS`. Retries 2× with 1s sleep (BT headphones are optional at boot; users can connect after startup).
- `bt_verify_connected()` — called at the top of `_speak_device()` on every TTS call; re-queries `bluealsa-aplay --list-pcms` and clears `_BT_AUDIO_DEV`/resets rate+channels if the device is no longer listed. Handles physical disconnects (headphones powered off/out of range) that `bt_disconnect()` would not catch.
- `bt_scan()` — 10s scan via `subprocess.run(['timeout', N, 'bluetoothctl', 'scan', 'on'])` (parses all output after completion — avoids readline hang on partial ANSI escape sequences from bluetoothctl); results stored in `_bt_scan_results`.
- `bt_pair(mac)` — pairs and trusts a device via bluetoothctl.
- `bt_connect(mac)` / `bt_disconnect(mac)` — connect/disconnect; calls `bt_detect_connected()` after connect to refresh `_BT_AUDIO_DEV`/format.
- Startup BT volume: raw 50/127 (~39%) via `amixer -D bluealsa cset numid=2 50` when headphones are detected at startup.
- `extract_bt_intent(text)` — detects 'scan'/'pair'/'connect'/'disconnect' from natural language.
- Natural language handled before LLM routing in both terminal and device mode.
- `/bt` slash command: `scan`, `pair <N>`, `<N>` to connect, `off` to disconnect.

## Phone Calls (HFP)

Zeev can make and receive phone calls via Bluetooth HFP (Hands-Free Profile) on the Pi. The phone acts as the source; the Pi acts as a hands-free unit.

- **HFP vs A2DP**: HFP is for bidirectional phone calls (uses SCO narrowband audio at 8 or 16 kHz); A2DP is for audio streaming headphones.
- **SCO audio device**: `bluealsa:DEV=<mac>,PROFILE=sco,SRV=org.bluealsa` — exposed by BlueALSA v4.3.1+ as ALSA PCM for both recording (capture) and playback.
- **`bt_speak_sco(text, sco_dev, samplerate)`** — speak text through the SCO device so the caller hears Zeev. Tries TTS in order: Groq Orpheus WAV → Piper one-shot raw PCM → gTTS MP3. All outputs are resampled via ffmpeg to the negotiated SCO rate and format (S16_LE, mono).
- **`bt_hfp_dev(mac)` / `bt_sco_rate(mac)` / `bt_hfp_detect()`** — query the SCO device string and sample rate; detect the phone's MAC from bluealsa output.
- **`bt_call_dial(number)` / `bt_call_hangup()`** — dial and hang up via AT commands (`ATD`, `AT+CHUP`) sent over D-Bus RFCOMM.
- **`bt_call_dtmf(mac, digit)`** — send DTMF tone (`AT+VTS=<digit>`) for IVR menu navigation instead of TTS speech.
- **`bt_call_loop(speak_fn, stt_fn, llm_fn, mac, record_dir, call_intent)`** — main call conversation loop:
  - Records caller audio via SCO capture + VAD (voice activity detection)
  - Detects call type on first turn: **voicemail** (voicemail greeting regex) → leave message + hangup; **IVR** (menu prompt regex) → wait for menu, reply with DTMF or listen for next prompt; **live/unknown** → normal conversation
  - For voicemail: extracts explicit message from call intent ("saying X") or generates via LLM; includes neutral default if neither
  - For IVR: uses focused system prompt ("reply ONLY with a single digit"); sends DTMF if LLM returns a digit; re-checks for voicemail mid-call (e.g., IVR → voicemail transfer)
  - For live calls: greeted with "Hello, this is Zeev, Alex's AI assistant." at call start (before listening); uses focused LLM prompt with only call_intent context (no memory facts injected)
  - Hangup detection skipped in IVR mode (IVR "Goodbye" is part of the menu, not call end)
  - Call recordings saved to `call_recordings/` dir per turn
- **`--no-greeting` flag** — suppresses startup TTS greeting (useful for testing calls via piped stdin without audio distraction).
- **Process lifecycle during calls**: If stdin closes (e.g., piped command), the REPL's exit handler waits for `_IN_CALL` to clear before exiting — ensures call loop runs to completion even if the user's input stream ends.
- **HFP guard**: Both terminal and device mode call paths call `bt_hfp_detect()` after the post-dial sleep; if it returns empty (phone not connected via HFP), the call is hung up and an error is spoken/printed instead of entering `bt_call_loop` with an invalid SCO device (which previously caused a hard hang).
- **Whisper hallucination filter**: `bt_call_loop` filters known Whisper hallucinations on ring tone / hold music ("Thank you.", "thanks", "please", "goodbye", etc.) before incrementing `turn` — prevents the call loop from treating ring noise as real speech.
- **Greeting deferred**: "Hello, this is Zeev, Alex's AI assistant." is spoken only when the call is classified as live/unknown — not before call type is known (voicemail and IVR don't need it).
- **Fast call detection** (`bt_fast_detect`): replaces VAD on turn 0. Records up to 6s; early-exits at ~3s if speech onset detected within 1.5s (real person said "Hello"). Transcribes with `groq_stt_call()` using `_CALL_WHISPER_PROMPT` (phone vocabulary bias) to suppress hallucinations. Early burst + short transcript (≤5 words, onset >100ms) → classified `live` immediately. Falls back to `detect_call_type()` LLM if still unknown. Cuts voicemail detection from 25s timeout to ~6-9s direct.
- **`_speech_onset_ms(pcm, samplerate)`** — finds millisecond offset of first speech-energy frame (RMS > 400). Onset at 0ms = pickup click/noise, not speech; threshold >100ms required for live-person heuristic.
- **`groq_stt_call(wav_bytes)`** — Whisper with `_CALL_WHISPER_PROMPT`: "Hello? Hi, who's this? You've reached. Please leave a message after the beep. Press 1 for. Thank you for calling." Biases transcription toward call vocabulary on 8kHz SCO audio.
- **Speculative pre-generation**: `bt_call_loop` starts a background thread immediately after dialing that pre-generates the voicemail message via `llm_fn` while the phone is ringing. Result stored in `_pregen_msg` list. All three voicemail message sites (turn 0 detection, mid-call IVR→voicemail transition, 25s timeout) check `_pregen_msg` first — if the message is ready, no LLM call is needed at speak time. Turn 0 site does `_pregen_thread.join(timeout=5)` to wait up to 5s if still generating. Cuts post-beep TTS latency from ~14s to ~5s.
- **SCO TTS chain order**: Groq Orpheus (daniel, male, WAV) → Cartesia (Kurt, male, ~100ms, WAV) → Piper Ryan (male, raw PCM) → gTTS (last resort). Cartesia uses `sonic-2` model via `https://api.cartesia.ai/tts/bytes`. Config: `CARTESIA_API_KEY` / `CARTESIA_VOICE_ID` in `.env`.

## Version Control

Never commit data files (e.g. `adult_jokes.json`, imported corpora) unless explicitly asked. Add generated/data files to `.gitignore` by default and confirm before committing.

## Shell Scripts / Deployment

When generating shell scripts or sudoers/inline commands for the Pi, watch for CRLF line endings from copy-paste; prefer inline commands or strip CRLF explicitly.

## Project: Zeev

A local AI companion running on a Raspberry Pi Zero 2W. It calls the [Groq](https://groq.com) API (OpenAI-compatible) for fast cloud inference and supports both a terminal REPL and a mobile-friendly web UI.

## Running

**Terminal REPL:**
```bash
python3 zeev/zeev.py
```

**Web server (HTTP, port 5000):**
```bash
python3 zeev/zeev.py --web
```

**Web server (HTTPS, port 5443):**
```bash
python3 zeev/zeev.py --https
```

Requires `GROQ_API_KEY` and `TAVILY_API_KEY`. Copy `.env.example` to `.env` and fill in your keys — `zeev.py` loads `.env` automatically at startup via a plain-text parser (no `python-dotenv` needed). `GROQ_API_KEY` is used for chat completions, TTS (Orpheus), and STT (Whisper). No other dependencies beyond the standard library and `requests` (`python3-requests` from apt).

## Architecture

Everything lives in `zeev/zeev.py` — a single-file script:

- **`stream_reply()`** — POSTs to Groq's `/v1/chat/completions` with `stream: true`, prints tokens as they arrive (terminal mode).
- **`load_prior()` / `append_message()`** — persistence via `data/zeev.db` SQLite (`messages` table, WAL mode). Loads the last `PRIOR_TURNS=15` turns (`ORDER BY id DESC LIMIT N`, reversed); caps in-session context at 60 messages.
- **`set_volume(level)` / `get_volume()`** — get/set system volume (0–100). `set_volume()` calls `amixer sset Master N%`; if that fails, falls back to `amixer -c wm8960soundcard sset Speaker` (raw 0–127). State stored in `_VOLUME` global (default 87).
- **`tavily_search(query)`** — calls Tavily API, returns up to 5 result snippets.
- **`needs_search(text)`** — returns True if the message matches `_SEARCH_RE`.
- **`_build_system_prompt(user_text, on_search)`** — assembles the per-turn system prompt: base persona + memory facts + RAG hits + optional Google Calendar events + optional Tavily results. `_with_search()` is an alias kept for compatibility.
- **`_groq_post(msgs, model, stream, max_tokens=400)`** — thin wrapper around the Groq API call. Token limit is model-aware: Torah queries and 70B/R1 models use 1200; 8B fast uses 600. On connection failure, `_llm_post()` automatically falls back to `_bosgame_stream()` if `BOSGAME_URL` is set.
- **`_bosgame_complete(msgs, max_tokens, json_mode)`** — non-streaming OpenAI-compatible POST to bosgame's Ollama via nginx proxy (`BOSGAME_URL/v1/chat/completions`). Uses `BOSGAME_MODEL` (default `llama3.2:1b`). Called by `extract_memory()` as the preferred (free, local) backend before falling back to Groq.
- **`_bosgame_stream(msgs, max_tokens)`** — streaming chat fallback via bosgame Ollama (`llama3.1:8b`). Uses `http.client.HTTPSConnection` directly (not `requests`) with a dedicated SSL context to avoid urllib3 pool conflicts. Connect timeout 10s; after connect, socket timeout extended to 300s for slow CPU inference. Returns a `_LineIter` wrapper with `iter_lines()` compatible with `_iter_llm_tokens(..., "groq")`.
- **`_llm_post(msgs, model, stream, max_tokens)`** — routes to the active LLM provider. For the `groq` provider: on connection errors (`NameResolution`, `Failed to resolve`, `Max retries`, `ConnectionError`, `Connection refused`), prints `[offline]` and falls back to `_bosgame_stream()`. Returns `(resp, err, provider)`.
- **`extract_memory(session_msgs)`** — prefers bosgame (`_bosgame_complete`, `llama3.2:1b`, ~5–10s on CPU) over Groq for fact extraction; falls back to Groq on error.
- **`route_model(text)`** — auto-selects model ID based on keyword heuristics (`_REASONING_RE`, `_SMART_RE`).
- **`run_web_server()`** — `ThreadingHTTPServer` serving a single-page chat UI (`_WEB_HTML`). Streams SSE tokens to the browser via `/chat`; `/clear` wipes the session; `/memory` returns stored facts; `/memorize` triggers fact extraction; `/tts` accepts POST `{"text": "..."}` and returns WAV audio via Groq Orpheus; `/transcribe` accepts raw audio bytes and returns `{"transcript": "..."}` via Groq Whisper; `/thermal` streams SSE with `{"thermal": {frame, min, max, center, hotspot_row, hotspot_col}}` then optional LLM tokens; `/thermal-status` returns `{"available": bool}`; `/volume` GET returns `{"volume": N}`, POST accepts `{"delta": ±N}` or `{"level": N}` and returns updated `{"volume": N}`.
- **`groq_tts(text)`** — calls Groq's `canopylabs/orpheus-v1-english` TTS model (`daniel` voice), returns WAV bytes or `None`. Returns `None` for non-English text (Orpheus is English-only).
- **`detect_lang(text)`** — returns `'he'` (Hebrew Unicode block), `'ru'` (Cyrillic block), `'es'` (ñ/¿/¡/accented vowels), or `'en'` as default.
- **`_clean_for_tts(text, lang=None)`** — strips markdown; replaces the Tetragrammaton with `אֲדֹנָי` (Hebrew) or `Adonai` (English) depending on `lang`.
- **`speak_terminal(text, lang=None)`** — optional `lang` override skips `detect_lang`; routes to Google Translate TTS + mpg123 (he/es/ru) or Piper (en). Runs in a background thread. Torah queries force `lang='he'`.
- **`_gtts_chunks(text)`** — splits text at sentence boundaries into ≤200-char chunks for Google Translate TTS.
- **`_gtts_fetch_chunk(chunk, lang)`** — fetches one MP3 chunk from `translate.googleapis.com/translate_tts` using `urllib.request` (not `requests`, to avoid shared urllib3 connection pool conflicts with the bosgame fallback stream). No API key needed.
- **`_gtts_speak(text, lang, adev=None)`** — plays Google Translate TTS via `mpg123` in a background thread; `adev` selects the ALSA device (used in device mode).
- **`init_tts()`** — detects Piper binary and populates `PIPER_MODELS` dict (`en`) by scanning `~/piper/` and `~/.local/share/piper/`. Falls back to espeak-ng. Sets `TTS_AVAILABLE`, `PIPER_BIN`, `PIPER_MODELS`.
- **`init_thermal()`** — tries to connect to the MLX90640 on I2C bus 3 (GPIO5/6 software I2C overlay). Sets `THERMAL_AVAILABLE`.
- **`_ensure_cert()`** — generates a self-signed TLS cert (SAN for local IP) when `--https` is used.
- **`_db()` / `_db_lock` / `_db_con`** — lazy singleton SQLite connection to `data/zeev.db` (`check_same_thread=False`, WAL mode). All storage calls acquire `_db_lock` before executing; this is the thread-safety mechanism for `ThreadingHTTPServer`. Tables: `messages`, `facts`, `notes`, `settings`.
- **`zeev_cleanup()`** — kills `_MUSIC_PROC` and `_piper_term_proc`, runs `pkill -f zeev_music` and `pkill -f zeev_rec.wav` to catch orphaned ffmpeg/mpg123/arecord processes, and removes `/tmp/zeev_*` temp files. Called at startup (clears crash leftovers) and in every shutdown path.
- **`main()`** — terminal REPL with `/clear`, `/forget`, `/model`, `/memory`, `/memorize`, `/forget-fact`, `/tts`, `/vol`, `/look`, `/thermal`, and `quit` commands; readline history in `data/.readline_history`. `/vol` accepts `+`, `-`, `up`, `down`, or a numeric 0–100 value. Speaks a time-of-day greeting to Alex on startup and "Goodbye, Alex." on exit via gTTS+mpg123 (blocking on exit so audio completes before the process dies).

### Models (Groq)

| Key | Model ID | Description |
|---|---|---|
| `1` | `llama-3.1-8b-instant` | Fast — casual chat, simple Q&A |
| `2` | `llama-3.3-70b-versatile` | Smart — explanations, code, writing |
| `3` | `deepseek-r1-distill-llama-70b` | Reasoning — math, logic, proofs |

Default is **auto-routing**: the model is chosen per message by `route_model()`. Use `/model` to lock to a specific one, or `0` to return to auto.

### Key constants

| Constant | Value | Purpose |
|---|---|---|
| `PRIOR_TURNS` | 15 | Turns loaded from disk into each new session |
| `max_tokens` | 600 / 1200 | Max reply length — 600 for 8B, 1200 for 70B/R1 and Torah queries |
| `temperature` | 0.75 | Sampling temperature |

### System prompt

`SYSTEM_PROMPT` is built at startup: the base persona string plus the contents of `swiftkey_system_prompt_snippet.md` (project root) if present. At runtime, `_build_system_prompt()` prepends user memory facts and RAG context before each Groq call.

### Smart model routing

`route_model(text)` picks a model per message using two keyword regexes — no extra API call:

- `_REASONING_RE` — matches math, logic, proofs, algorithms, calculus/algebra/geometry → DeepSeek R1
- `_SMART_RE` — matches code, explanations, summaries, comparisons, and natural-language question patterns ("tell me about", "what is/are", "why does/is/are", "history of", "how to", "pros and cons", "recommend", etc.) → 70B Smart
- Default → 8B Fast

The terminal prints `[auto → 8B/70B/R1]` before each response. The web UI shows the model as a dim tag below the bubble. Lock/unlock with `/model`.

### Google Calendar

`gcal_fetch(days=1)` reads `data/gcal_token.json` (OAuth2 credentials from `gcal_auth.py` one-time flow), auto-refreshes the access token when expired (writes updated token back), and fetches events from the Google Calendar API for the next `days` calendar days. Results are cached in-process for 5 minutes per unique `days` value. `needs_calendar(text)` triggers on keywords like "calendar", "schedule", "meeting", "agenda", "am I free", etc. — silently skips if `gcal_token.json` is absent. `gcal_days_from_query(text)` parses natural-language range cues: "tomorrow"→2, "this week"→7, "next week"→14, "next N days"→N, "this month"→30; defaults to 1. When triggered, `_build_system_prompt()` injects events as `## Today's calendar:` (1 day) or `## Calendar (next N days):` (multi-day). Multi-day results prefix each event with weekday (`Mon 3:00 PM`). `maxResults` scales with range (up to 50). No google-auth library required — uses `requests` directly.

### Web search

Zeev uses a keyword heuristic (`_SEARCH_RE`) to decide when to search — no model tool-use. Flow:
1. If the user message matches `_SEARCH_RE` (words like "weather", "news", "today", "latest", "price", etc.) and `TAVILY_API_KEY` is set, call `tavily_search()`
2. Inject results into the system prompt for that turn
3. Stream the Groq response as normal

Both terminal (prints `[searching: query]`) and web UI (sends `{"info": ...}` SSE event) show a status line during search.

### User memory (persistent facts)

Facts about the user are extracted from conversations and stored in `data/zeev.db` (`facts` table). Injected into every system prompt under `## What I know about Alex:`.

- **`load_memory()` / `save_memory()`** — read/write the `facts` table in `zeev.db` (unique text rows, ordered by insertion `id`).
- **`extract_memory(session_msgs)`** — calls Groq (`llama-3.1-8b-instant`, `response_format: json_object`) with a transcript of the session to extract new facts. Deduplicates against existing facts. Returns `None` on 429 rate-limit.
- Extraction runs automatically on `quit` in terminal mode. Can also be triggered with `/memorize` (terminal) or the 🧠 → "Memorize this session" button (web UI).
- Remove individual facts with `/forget-fact N` (terminal) or via the memory panel (web UI).

### History RAG

Past conversations are indexed at startup for keyword-based retrieval. Relevant exchanges are injected into the system prompt for each turn.

- **`build_rag_index()`** — reads all rows from the `messages` table (`ORDER BY id`) into `_HISTORY_ENTRIES` and builds an inverted word index (`_HISTORY_INDEX`), filtering stop words (`_STOP_WORDS`).
- **`retrieve_relevant(query, k=2, min_score=2)`** — scores past entries by word overlap with the current query, returns up to `k` `(user_msg, assistant_reply)` pairs above `min_score`. Injected as `## Relevant past exchanges:`.
- **`init_learning()`** — called once at startup by both `main()` and `run_web_server()`; loads memory and builds RAG index.

### Torah RAG (Sefaria)

Local SQLite FTS5 database spanning Tanakh, Mishna, Talmud, Apocrypha, Liturgy, Zohar, Dead Sea Scrolls, and Sumerian literature. Populated by `zeev/import_sefaria.py` (resume-safe, ~75 min for the full corpus).

- **`needs_torah(text)`** — returns True if the message matches `_TORAH_RE` (Torah, Talmud, Gemara, halacha, Apocrypha book names, liturgy terms, Zohar/Kabbalah, DSS/Qumran, Sumerian/Gilgamesh, etc.).
- **`torah_search(query, k=3)`** — FTS5 full-text search over `data/torah.db`; returns up to `k` `(ref, en_text)` pairs. Injected as `## Relevant Torah/Talmud passages:`.
- `_build_system_prompt()` calls `torah_search()` whenever `needs_torah()` is true.
- DB schema: `passages` FTS5 table with columns `source` (Tanakh/Mishna/Gemara/Apocrypha/Siddur/Haggadah/Zohar/DSS/Sumerian), `ref`, `en`, `he`. `done` table tracks imported refs for resume safety.
- **Apocrypha**: Ben Sira (51 ch), Tobit (13), Judith (16), 1 Maccabees (16), 2 Maccabees (10), Wisdom of Solomon (18), Prayer of Manasseh (1), Psalm 151. Ben Sira chapters 17/22–24/29/36 use `a`–`g` sub-refs (`_BEN_SIRA_SPLIT`).
- **Liturgy**: Siddur Ashkenaz (456 sections), Pesach Haggadah, The Jonathan Sacks Haggadah. Sections discovered by walking the Sefaria index tree (`_fetch_index` + `_walk_leaf_sections`); each section fetched as one unit (all paragraphs at once).
- **Zohar**: ~1806 chapters across all parshiyot + Idra Rabba/Zuta, Sifra DiTzniuta, Addenda. Chapter counts from `index_offsets_by_depth`. Chapters with no EN translation are marked done and skipped.
- **Dead Sea Scrolls**: ~11,000 fragments in Hebrew/Aramaic from [ETCBC/dss](https://github.com/ETCBC/dss) (Martin Abegg's transcriptions, Text-Fabric format). `import_dss()` fetches 5 TF feature files (scroll.tf, fragment.tf, full.tf, after.tf, oslots.tf), parses oslots in a single pass to get word→line first-sign mapping, groups words by scroll+fragment via bisect. `scroll.tf`/`fragment.tf` are annotated per LINE node (not fragment node).
- **Sumerian**: 381 ETCSL texts (myths, hymns, Gilgamesh, royal praise, lamentations, proverbs) via a single JSON fetch from GitHub. `import_sumerian()` strips HTML from paragraph content.

### Quantum reasoning

`quantum_reason(idea, llm_fn, past_insights=None)` in `zeev/quantum.py` runs the full pipeline: idea → circuit spec → simulate → interpret. `past_insights` (list of `{idea, interpretation}` dicts) is injected into the interpretation prompt to compound learning over time.

- **`zeev/quantum_daily.py`** — 8 canonical human-dilemma scenarios, one runs per day (selected by `day-of-year % 8`). Saves `idea`, `spec_json`, `result_json`, and `interpretation` to the `quantum_insights` table in `zeev.db`. `--all` flag runs every scenario. Cron: `0 6 * * *` on the Pi, logs to `zeev/data/quantum_daily.log`.
- **`save_quantum_insight(idea, spec, result, interpretation)`** — writes one row to `quantum_insights`.
- **`load_quantum_insights(k=3)`** — returns the `k` most recent insights (newest first) for injection into the next run's interpretation prompt.
- All three `quantum_reason()` call sites (web handler, device mode `/quantum`, terminal REPL) load `k=3` past insights before running and save the result afterward.
- `quantum_insights` table is part of `zeev.db` schema, auto-created by `_db()`.

### Music playback

- **`youtube_play(query, adev=None)`** — searches YouTube via `yt-dlp --default-search ytsearch1`, downloads best audio, pipes through `ffmpeg` → `mpg123`. Returns `(title, error)`.
- Terminal: `play <query>` keyword detected before model routing. `/stop` kills the playback process.
- The user can say natural language like `play some jazz` or `stop the music`.

### Web UI features

- Dark mobile-first chat interface
- Model selector (Auto / 8B / 70B / DeepSeek R1) — Auto is default; model tag shown on each bubble
- 🧠 memory panel — view stored facts, trigger memorization
- TTS: Groq Orpheus (`daniel`) for English; Google Translate TTS (served as MP3 from `/tts`) for he/es/ru; browser `speechSynthesis` as last resort if gTTS fails
- Chat bubbles and input field use `dir="auto"` for automatic RTL layout with Hebrew
- Voice input: tap-to-speak (Web Speech Recognition, auto-sends on silence) via 🎤 button
- Continuous recording: tap ⏺ to start, tap again to stop → audio sent to `/transcribe` (Groq `whisper-large-v3-turbo`) → transcript auto-sent as message
- 🌡 thermal camera button (shown when MLX90640 detected) — renders a 32×24 canvas heatmap (blue→red gradient, white crosshair on hotspot) with min/max/center stats; optional question sent to LLM
- Volume control — `🔉` / `🔊` buttons in the header with a live `N%` readout; calls `POST /volume {"delta": ±10}` and syncs on page load via `GET /volume`
- HTTPS mode with auto-generated self-signed cert (accept once in browser)

### Multilingual TTS

`detect_lang()` inspects each reply before speaking:

| Language | Detection | Terminal/device engine | Web UI engine |
|---|---|---|---|
| English | default | Piper `en_US-lessac-medium` | Groq Orpheus `daniel` |
| Spanish | ñ, ¿, ¡, accented vowels | Google Translate TTS + mpg123 | Google Translate TTS (MP3) → `speechSynthesis` `es-MX` |
| Hebrew | any Hebrew Unicode character | Google Translate TTS + mpg123 | Google Translate TTS (MP3) → `speechSynthesis` `he-IL` |
| Russian | Cyrillic block (U+0400–U+04FF) | Google Translate TTS + mpg123 | Google Translate TTS (MP3) → `speechSynthesis` `ru-RU` |

`detect_lang()` uses `.search()` — a single Hebrew character in the response is enough to trigger Hebrew gTTS. Torah/Sefaria query replies also force `lang='he'` regardless of reply script.

Groq Orpheus only supports English; non-English replies return `None` from `groq_tts()`. The `/tts` endpoint then tries Google Translate TTS (returned as `audio/mpeg`) before falling back to a `503 {"lang": ...}` response that tells the browser to use `speechSynthesis`. Text is split into ≤200-char chunks at sentence boundaries.

### Web UI SSE events

| Event | Meaning |
|---|---|
| `{"token": "..."}` | Streamed reply chunk |
| `{"model": "8B"}` | Auto-routed model label (sent before first token) |
| `{"info": "..."}` | Status message (e.g. search in progress) |
| `{"error": "..."}` | Error from server |
| `{"thermal": {...}}` | Thermal frame data: `frame` (768 floats °C), `min`, `max`, `center`, `hotspot_row`, `hotspot_col` |
| `{"image": "data:image/jpeg;..."}` | Camera snapshot (from `/snap`) |

### File layout

```
zeev/
  zeev.py                        # entire application
  mlx90640.py                    # MLX90640 thermal camera helper (I2C bus 3)
  import_sefaria.py              # populate data/torah.db (Sefaria, ETCBC DSS, ETCSL Sumerian)
  setup.sh                       # one-time setup script (legacy llama.cpp)
  migrate_to_sqlite.py           # idempotent flat-file → zeev.db import (run once)
  test_sqlite_migration.py       # 38-test regression harness (flat-file vs SQLite parity)
  quantum_daily.py               # daily quantum teaching: 8 scenarios, cron 0 6 * * *
  data/                          # runtime files (git-ignored)
    zeev.db                      # WAL-mode SQLite: messages, facts, notes, settings, quantum_insights
    quantum_daily.log            # stdout from cron runs of quantum_daily.py
    torah.db                     # FTS5 corpus DB (Tanakh/Mishna/Gemara/Zohar/DSS/Sumerian/…)
    .readline_history            # terminal readline history
    cert.pem / key.pem           # TLS certs (--https mode)
    piper_voice.onnx             # optional: drop a Piper model here for auto-detection
~/piper/                         # Piper TTS install (outside repo)
  piper                          # binary (symlinked to ~/.local/bin/piper)
  en_US-lessac-medium.onnx       # English voice (auto-detected by init_tts)
  *.onnx.json                    # voice config
swiftkey_system_prompt_snippet.md  # personal vocabulary appended to system prompt
```

### Thermal camera (MLX90640)

The MLX90640 32×24 thermal camera is connected to the software I2C bus on GPIO5 (SDA) / GPIO6 (SCL), configured in `/boot/firmware/config.txt` as:

```
dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=5,i2c_gpio_scl=6,i2c_gpio_delay_us=10
```

It appears at `/dev/i2c-3`, address `0x33`. The hardware I2C bus (`/dev/i2c-1`) is used by the WM8960 audio codec at address `0x19`.

All thermal camera logic lives in `zeev/mlx90640.py`:
- `init_thermal()` — connects via a `smbus2`-backed busio shim (Adafruit's blinka can't route to bus 3 automatically)
- `capture_frame()` — returns 768 calibrated °C floats via `adafruit_mlx90640`
- `frame_summary(frame)` — `{min, max, center, hotspot_row, hotspot_col}`
- `ascii_map(frame)` — ANSI 24-bit color ASCII heatmap for the terminal

### Whisplay HAT device mode

`python3 zeev/zeev.py --device` runs a push-to-talk voice companion on the PiSugar Whisplay HAT (1.96" ST7789 LCD 240×280, WM8960 audio codec, RGB LED, KEY button on GPIO17).

- **TTS priority**: Groq Orpheus (cloud, English) → Google Translate TTS + mpg123 (he/es/ru) → Piper (en fallback, one-shot when BT connected, persistent for speaker) → espeak-ng (last resort)
- **Speaker volume**: set to raw 110 (~87%) via `amixer` at startup (`hw:wm8960soundcard`, `Speaker` control, raw range 0–127)
- **BT headphone volume**: set to raw 50/127 (~39%) via `amixer -D bluealsa` at startup when headphones are detected
- **Recording**: `arecord -f S16_LE -r 16000 -c 1` on `plughw:wm8960soundcard,0`
- **STT**: Groq Whisper `whisper-large-v3-turbo`
- Driver install: `cd ~/Whisplay && sudo bash install_driver.sh && sudo reboot`

## Hardware context

Target device is a **Raspberry Pi Zero 2W** (512 MB RAM, 4× ARM Cortex-A53). Chat inference runs on Groq's cloud. Device mode TTS uses Groq Orpheus (cloud) for English and Google Translate TTS for he/es/ru — both start in ~500ms (no model load). Terminal English TTS uses local Piper (persistent process, no reload delay after first call); Piper's ONNX model takes ~20s to load on cold start, so the startup greeting and shutdown farewell use gTTS+mpg123 directly for fast playback. Web UI TTS is Groq Orpheus (English) or Google Translate TTS (he/es/ru, returned as MP3), with browser `speechSynthesis` as last resort.

### Startup / shutdown behaviour

- `zeev_cleanup()` runs at the top of `main()`, `run_web_server()`, and `run_device_mode()` to kill stale processes and temp files from any previous crash.
- On startup, `main()` speaks **"Good morning/afternoon/evening, Alex. Ready when you are."** via gTTS+mpg123 (background thread, plays within ~2s).
- On exit (`quit`, Ctrl-C, or SIGTERM), `main()` speaks **"Goodbye, Alex."** synchronously before calling `sys.exit()`.
- Journald is configured for persistent storage (`/var/log/journal/`) so logs survive reboots and `journalctl -b -1` works.

### bosgame Ollama integration

bosgame (`Maccabeus-Ecolite-Series`, LAN IP `10.0.0.141`) runs Ollama and acts as a free local inference backend. The Pi reaches it via an nginx reverse proxy on `sogdiana-gematria.net`:

- **Endpoint**: `https://ollama.sogdiana-gematria.net/ollama/` (grey-cloud DNS → `10.0.0.141` direct, bypasses Cloudflare)
- **Auth**: `X-Zeev-Key` header; value in `.env` as `BOSGAME_KEY`
- **`.env` keys**: `BOSGAME_URL=https://ollama.sogdiana-gematria.net/ollama`, `BOSGAME_MODEL=llama3.2:1b`, `BOSGAME_KEY=<hash>`
- **Models available**: `llama3.1:8b` (chat fallback), `llama3.2:1b` (memory extraction, ~5–10s on CPU)
- **Pi `/etc/hosts`**: `10.0.0.141 ollama.sogdiana-gematria.net` — required to avoid NAT hairpin (Pi on LAN cannot reach the public IP). Entry is persisted via `/etc/cloud/templates/hosts.debian.tmpl` (cloud-init manages `/etc/hosts` and resets it on reboot without this).
- **Offline coverage**: fallback only works when Pi is on the home LAN. Away from home with no WiFi = no fallback (bosgame unreachable). Use phone hotspot for mobile use.
- **nginx config** (bosgame `/etc/nginx/sites-available/default`): `/ollama/` location with `proxy_buffering off`, `proxy_read_timeout 300s`, auth via `$http_x_zeev_key`.

### User

The user's name is **Alex** (Linux username is `ragnar`). Always address them as Alex in greetings and anywhere the user's name appears in TTS or prompts.
