# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
- **`load_prior()` / `append_message()`** — persistence via `data/history.jsonl` (JSONL, one `{role, content, ts}` per line). Loads the last `PRIOR_TURNS=15` turns at startup; caps in-session context at 60 messages.
- **`tavily_search(query)`** — calls Tavily API, returns up to 5 result snippets.
- **`needs_search(text)`** — returns True if the message matches `_SEARCH_RE`.
- **`_build_system_prompt(user_text, on_search)`** — assembles the per-turn system prompt: base persona + memory facts + RAG hits + optional Tavily results. `_with_search()` is an alias kept for compatibility.
- **`_groq_post(msgs, model, stream)`** — thin wrapper around the Groq API call.
- **`route_model(text)`** — auto-selects model ID based on keyword heuristics (`_REASONING_RE`, `_SMART_RE`).
- **`run_web_server()`** — `ThreadingHTTPServer` serving a single-page chat UI (`_WEB_HTML`). Streams SSE tokens to the browser via `/chat`; `/clear` wipes the session; `/memory` returns stored facts; `/memorize` triggers fact extraction; `/tts` accepts POST `{"text": "..."}` and returns WAV audio via Groq Orpheus; `/transcribe` accepts raw audio bytes and returns `{"transcript": "..."}` via Groq Whisper; `/thermal` streams SSE with `{"thermal": {frame, min, max, center, hotspot_row, hotspot_col}}` then optional LLM tokens; `/thermal-status` returns `{"available": bool}`.
- **`groq_tts(text)`** — calls Groq's `canopylabs/orpheus-v1-english` TTS model (`daniel` voice), returns WAV bytes or `None`. Returns `None` for non-English text (Orpheus is English-only).
- **`detect_lang(text)`** — returns `'he'` (Hebrew Unicode block), `'ru'` (Cyrillic block), `'es'` (ñ/¿/¡/accented vowels), or `'en'` as default.
- **`_clean_for_tts(text)`** — strips markdown before passing text to any TTS engine.
- **`speak_terminal(text)`** — detects language, routes to the appropriate Piper model (en/es/ru) or espeak-ng (he, or fallback). Runs in a background thread.
- **`init_tts()`** — detects Piper binary and populates `PIPER_MODELS` dict (`en`, `es`, `ru`) by scanning `~/piper/` and `~/.local/share/piper/`. Falls back to espeak-ng. Sets `TTS_AVAILABLE`, `PIPER_BIN`, `PIPER_MODELS`.
- **`init_thermal()`** — tries to connect to the MLX90640 on I2C bus 3 (GPIO5/6 software I2C overlay). Sets `THERMAL_AVAILABLE`.
- **`_ensure_cert()`** — generates a self-signed TLS cert (SAN for local IP) when `--https` is used.
- **`main()`** — terminal REPL with `/clear`, `/forget`, `/model`, `/memory`, `/memorize`, `/forget-fact`, `/tts`, `/look`, `/thermal`, and `quit` commands; readline history in `data/.readline_history`.

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
| `max_tokens` | 400 | Max reply length |
| `temperature` | 0.75 | Sampling temperature |

### System prompt

`SYSTEM_PROMPT` is built at startup: the base persona string plus the contents of `swiftkey_system_prompt_snippet.md` (project root) if present. At runtime, `_build_system_prompt()` prepends user memory facts and RAG context before each Groq call.

### Smart model routing

`route_model(text)` picks a model per message using two keyword regexes — no extra API call:

- `_REASONING_RE` — matches math, logic, proofs, algorithms → DeepSeek R1
- `_SMART_RE` — matches code, explanations, summaries, comparisons → 70B Smart
- Default → 8B Fast

The terminal prints `[auto → 8B/70B/R1]` before each response. The web UI shows the model as a dim tag below the bubble. Lock/unlock with `/model`.

### Web search

Zeev uses a keyword heuristic (`_SEARCH_RE`) to decide when to search — no model tool-use. Flow:
1. If the user message matches `_SEARCH_RE` (words like "weather", "news", "today", "latest", "price", etc.) and `TAVILY_API_KEY` is set, call `tavily_search()`
2. Inject results into the system prompt for that turn
3. Stream the Groq response as normal

Both terminal (prints `[searching: query]`) and web UI (sends `{"info": ...}` SSE event) show a status line during search.

### User memory (persistent facts)

Facts about the user are extracted from conversations and stored in `data/user_memory.json`. They are injected into every system prompt under `## What I know about Ragnar:`.

- **`load_memory()` / `save_memory()`** — read/write `data/user_memory.json` (JSON list of strings).
- **`extract_memory(session_msgs)`** — calls Groq (`llama-3.1-8b-instant`, `response_format: json_object`) with a transcript of the session to extract new facts. Deduplicates against existing facts. Returns `None` on 429 rate-limit.
- Extraction runs automatically on `quit` in terminal mode. Can also be triggered with `/memorize` (terminal) or the 🧠 → "Memorize this session" button (web UI).
- Remove individual facts with `/forget-fact N` (terminal) or via the memory panel (web UI).

### History RAG

Past conversations are indexed at startup for keyword-based retrieval. Relevant exchanges are injected into the system prompt for each turn.

- **`build_rag_index()`** — parses `data/history.jsonl` into an inverted word index (`_HISTORY_INDEX`) at startup, filtering stop words (`_STOP_WORDS`).
- **`retrieve_relevant(query, k=2, min_score=2)`** — scores past entries by word overlap with the current query, returns up to `k` `(user_msg, assistant_reply)` pairs above `min_score`. Injected as `## Relevant past exchanges:`.
- **`init_learning()`** — called once at startup by both `main()` and `run_web_server()`; loads memory and builds RAG index.

### Web UI features

- Dark mobile-first chat interface
- Model selector (Auto / 8B / 70B / DeepSeek R1) — Auto is default; model tag shown on each bubble
- 🧠 memory panel — view stored facts, trigger memorization
- TTS: Groq Orpheus (`daniel`) for English; browser `speechSynthesis` fallback for Spanish (`es-MX`), Hebrew (`he-IL`), Russian (`ru-RU`)
- Chat bubbles and input field use `dir="auto"` for automatic RTL layout with Hebrew
- Voice input: tap-to-speak (Web Speech Recognition, auto-sends on silence) via 🎤 button
- Continuous recording: tap ⏺ to start, tap again to stop → audio sent to `/transcribe` (Groq `whisper-large-v3-turbo`) → transcript auto-sent as message
- 🌡 thermal camera button (shown when MLX90640 detected) — renders a 32×24 canvas heatmap (blue→red gradient, white crosshair on hotspot) with min/max/center stats; optional question sent to LLM
- HTTPS mode with auto-generated self-signed cert (accept once in browser)

### Multilingual TTS

`detect_lang()` inspects each reply before speaking:

| Language | Detection | Terminal engine | Web UI engine |
|---|---|---|---|
| English | default | Piper `en_US-lessac-medium` | Groq Orpheus `daniel` |
| Spanish | ñ, ¿, ¡, accented vowels | Piper `es_MX-ald-medium` | `speechSynthesis` `es-MX` |
| Hebrew | Hebrew Unicode block (U+0590–U+05FF) | espeak-ng `-v he` | `speechSynthesis` `he-IL` |
| Russian | Cyrillic block (U+0400–U+04FF) | Piper `ru_RU-dmitri-medium` | `speechSynthesis` `ru-RU` |

Groq Orpheus only supports English; non-English replies return `None` from `groq_tts()` and the web UI falls back to `speechSynthesis` with the correct BCP-47 language tag. Hebrew chat bubbles and the input field use `dir="auto"` for automatic RTL layout.

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
  setup.sh                       # one-time setup script (legacy llama.cpp)
  data/                          # runtime files (git-ignored)
    history.jsonl                # conversation history (JSONL)
    user_memory.json             # persistent user facts
    .readline_history            # terminal readline history
    cert.pem / key.pem           # TLS certs (--https mode)
    piper_voice.onnx             # optional: drop a Piper model here for auto-detection
~/piper/                         # Piper TTS install (outside repo)
  piper                          # binary (symlinked to ~/.local/bin/piper)
  en_US-lessac-medium.onnx       # English voice (auto-detected by init_tts)
  es_MX-ald-medium.onnx          # Spanish voice
  ru_RU-dmitri-medium.onnx       # Russian voice
  *.onnx.json                    # voice configs (one per model)
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

- **TTS priority**: Groq Orpheus (cloud, fast, English) → Piper (local neural) → espeak-ng (fallback)
- **Speaker volume**: set to 75% via `amixer` at startup (`hw:wm8960soundcard`, `Speaker` control)
- **Recording**: `arecord -f S16_LE -r 16000 -c 1` on `plughw:wm8960soundcard,0`
- **STT**: Groq Whisper `whisper-large-v3-turbo`
- Driver install: `cd ~/Whisplay && sudo bash install_driver.sh && sudo reboot`

## Hardware context

Target device is a **Raspberry Pi Zero 2W** (512 MB RAM, 4× ARM Cortex-A53). Chat inference runs on Groq's cloud. Device mode TTS uses Groq Orpheus (cloud) for low latency; terminal TTS uses local Piper (model load ~7s, then a few seconds per response); web UI TTS is also Groq Orpheus.
