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

Requires `GROQ_API_KEY` and `TAVILY_API_KEY` environment variables (both in `~/.bashrc`). No other dependencies beyond the standard library and `requests` (`python3-requests` from apt).

## Architecture

Everything lives in `zeev/zeev.py` — a single-file script:

- **`stream_reply()`** — POSTs to Groq's `/v1/chat/completions` with `stream: true`, prints tokens as they arrive (terminal mode).
- **`load_prior()` / `append_message()`** — persistence via `data/history.jsonl` (JSONL, one `{role, content, ts}` per line). Loads the last `PRIOR_TURNS=15` turns at startup; caps in-session context at 60 messages.
- **`tavily_search(query)`** — calls Tavily API, returns up to 5 result snippets.
- **`needs_search(text)`** — returns True if the message matches `_SEARCH_RE`.
- **`_build_system_prompt(user_text, on_search)`** — assembles the per-turn system prompt: base persona + memory facts + RAG hits + optional Tavily results. `_with_search()` is an alias kept for compatibility.
- **`_groq_post(msgs, model, stream)`** — thin wrapper around the Groq API call.
- **`route_model(text)`** — auto-selects model ID based on keyword heuristics (`_REASONING_RE`, `_SMART_RE`).
- **`run_web_server()`** — `ThreadingHTTPServer` serving a single-page chat UI (`_WEB_HTML`). Streams SSE tokens to the browser via `/chat`; `/clear` wipes the session; `/memory` returns stored facts; `/memorize` triggers fact extraction; `/tts` accepts POST `{"text": "..."}` and returns WAV audio via Groq Orpheus.
- **`groq_tts(text)`** — calls Groq's `canopylabs/orpheus-v1-english` TTS model (`daniel` voice), returns WAV bytes or `None` on error.
- **`_clean_for_tts(text)`** — strips markdown before passing text to any TTS engine.
- **`speak_terminal(text)`** — speaks via Piper (preferred) or espeak-ng (fallback) in a background thread. Piper binary and model are auto-detected by `init_tts()`.
- **`init_tts()`** — detects Piper binary (`piper` in PATH) and model file (checks `data/piper_voice.onnx`, `~/piper/en_US-lessac-medium.onnx`, `~/.local/share/piper/`); falls back to espeak-ng. Sets `TTS_AVAILABLE`, `PIPER_BIN`, `PIPER_MODEL`.
- **`_ensure_cert()`** — generates a self-signed TLS cert (SAN for local IP) when `--https` is used.
- **`main()`** — terminal REPL with `/clear`, `/forget`, `/model`, `/memory`, `/memorize`, `/forget-fact`, `/tts`, and `quit` commands; readline history in `data/.readline_history`.

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
- TTS via Groq Orpheus (`daniel` voice) — called after each reply finishes, played via browser Audio API
- Voice input (Web Speech Recognition)
- HTTPS mode with auto-generated self-signed cert (accept once in browser)

### Web UI SSE events

| Event | Meaning |
|---|---|
| `{"token": "..."}` | Streamed reply chunk |
| `{"model": "8B"}` | Auto-routed model label (sent before first token) |
| `{"info": "..."}` | Status message (e.g. search in progress) |
| `{"error": "..."}` | Error from server |

### File layout

```
zeev/
  zeev.py                        # entire application
  setup.sh                       # one-time setup script (legacy llama.cpp)
  data/                          # runtime files (git-ignored)
    history.jsonl                # conversation history (JSONL)
    user_memory.json             # persistent user facts
    .readline_history            # terminal readline history
    cert.pem / key.pem           # TLS certs (--https mode)
    piper_voice.onnx             # optional: drop a Piper model here for auto-detection
~/piper/                         # Piper TTS install (outside repo)
  piper                          # binary (symlinked to ~/.local/bin/piper)
  en_US-lessac-medium.onnx       # voice model (auto-detected by init_tts)
  en_US-lessac-medium.onnx.json  # voice config
swiftkey_system_prompt_snippet.md  # personal vocabulary appended to system prompt
```

## Hardware context

Target device is a **Raspberry Pi Zero 2W** (512 MB RAM, 4× ARM Cortex-A53). Chat inference runs on Groq's cloud. Terminal TTS runs locally via Piper (model load ~7s, then a few seconds per response); web UI TTS is also cloud-based via Groq Orpheus so it does not burden the Pi.
