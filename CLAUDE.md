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

Requires `GROQ_API_KEY` environment variable. No other dependencies beyond the standard library and `requests` (`python3-requests` from apt).

## Architecture

Everything lives in `zeev/zeev.py` — a single-file script:

- **`stream_reply()`** — POSTs to Groq's `/v1/chat/completions` with `stream: true`, prints tokens as they arrive (terminal mode).
- **`load_prior()` / `append_message()`** — persistence via `data/history.jsonl` (JSONL, one `{role, content, ts}` per line). Loads the last `PRIOR_TURNS=15` turns at startup; caps in-session context at 60 messages.
- **`run_web_server()`** — `ThreadingHTTPServer` serving a single-page chat UI (`_WEB_HTML`). Streams SSE tokens to the browser via `/chat`; `/clear` wipes the session.
- **`_ensure_cert()`** — generates a self-signed TLS cert (SAN for local IP) when `--https` is used.
- **`main()`** — terminal REPL with `/clear`, `/forget`, `/model`, and `quit` commands; readline history in `data/.readline_history`.

### Models (Groq)

| Key | Model ID | Description |
|---|---|---|
| `1` (default) | `llama-3.1-8b-instant` | Fast |
| `2` | `llama-3.3-70b-versatile` | Smart |
| `3` | `deepseek-r1-distill-llama-70b` | Reasoning |

### Key constants

| Constant | Value | Purpose |
|---|---|---|
| `PRIOR_TURNS` | 15 | Turns loaded from disk into each new session |
| `max_tokens` | 400 | Max reply length |
| `temperature` | 0.75 | Sampling temperature |

### System prompt

`SYSTEM_PROMPT` is built at startup: the base persona string plus the contents of `swiftkey_system_prompt_snippet.md` (project root) if present. That file contains the user's personal vocabulary for correct name/term recognition.

### Web UI features

- Dark mobile-first chat interface
- Model selector (8B / 70B / DeepSeek R1)
- TTS (Web Speech API) with sentence-level streaming
- Voice input (Web Speech Recognition)
- HTTPS mode with auto-generated self-signed cert (accept once in browser)

### File layout

```
zeev/
  zeev.py                        # entire application
  setup.sh                       # one-time setup script (legacy llama.cpp)
  data/                          # history.jsonl + .readline_history + TLS certs (runtime)
swiftkey_system_prompt_snippet.md  # personal vocabulary appended to system prompt
```

## Hardware context

Target device is a **Raspberry Pi Zero 2W** (512 MB RAM, 4× ARM Cortex-A53). Inference runs on Groq's cloud, so local hardware only handles the HTTP server and UI — no local model loading required.
