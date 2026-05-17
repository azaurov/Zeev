# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: Zeev

A local AI companion running on a Raspberry Pi Zero 2W. It runs a `llama-server` subprocess (llama.cpp b9186 ARM64 binary) serving Qwen2.5-1.5B-Instruct-Q4_K_M, then chats with it over a local OpenAI-compatible HTTP API.

## Running

```bash
python3 zeev/zeev.py
```

First-time setup (downloads model ~930 MB and llama.cpp binaries):

```bash
bash zeev/setup.sh
```

No dependencies beyond the standard library and `requests` (`python3-requests` from apt).

## Architecture

Everything lives in `zeev/zeev.py` — a single-file script:

- **`start_server()`** — spawns `bin/llama-server` with `LD_LIBRARY_PATH` set to `bin/` (where the `.so` files live), waits up to 90 × 2 s for `/health` to return `ok`.
- **`stream_reply()`** — POSTs to `/v1/chat/completions` with `stream: true`, prints tokens as they arrive.
- **`load_prior()` / `append_message()`** — persistence via `data/history.jsonl` (JSONL, one `{role, content, ts}` per line). Loads the last `PRIOR_TURNS=15` turns (30 lines) at startup; caps in-session context at 60 messages.
- **`main()`** — REPL loop with `/clear`, `/forget`, and `quit` commands; readline history in `data/.readline_history`.

### Key constants

| Constant | Value | Purpose |
|---|---|---|
| `PRIOR_TURNS` | 15 | Turns loaded from disk into each new session |
| `max_tokens` | 400 | Max reply length |
| `temperature` | 0.75 | Sampling temperature |
| `-c` (context) | 2048 | llama-server context window |
| `-t` (threads) | 4 | CPU threads (Pi Zero 2W has 4 cores) |
| `-b` (batch) | 256 | Prompt eval batch size |

### File layout

```
zeev/
  zeev.py          # entire application
  setup.sh         # one-time setup script
  bin/             # llama-server + shared libs (ARM64, not in git)
  models/          # qwen2.5-1.5b-instruct-q4_k_m.gguf (not in git)
  data/            # history.jsonl + .readline_history (created at runtime)
```

## Hardware context

Target device is a **Raspberry Pi Zero 2W** (512 MB RAM, 4× ARM Cortex-A53). The 3 GB swap file on SD card is mandatory — model loading exceeds physical RAM. Inference is slow (~1–3 tok/s); the 90-second server startup timeout exists for this reason.
