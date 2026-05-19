# Zeev

A personal AI companion running on a **Raspberry Pi Zero 2W**. Zeev uses [Groq](https://groq.com) for fast cloud inference and speaks back in human-quality voices. Supports a terminal REPL and a mobile-friendly web UI.

## Features

- **Fast chat** — auto-routes each message to the right model (8B fast, 70B smart, DeepSeek R1 for reasoning)
- **Web search** — keyword heuristic triggers [Tavily](https://tavily.com) search and injects results into the reply
- **Persistent memory** — extracts facts about you from conversations and recalls them on every turn
- **History RAG** — keyword-indexes past conversations and injects relevant exchanges into context
- **Multilingual TTS** — speaks English, Spanish, Russian, and Hebrew with distinct voices
- **Voice input** — Web Speech Recognition in the browser
- **Mobile web UI** — dark, responsive single-page chat with streaming tokens

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

Requires `GROQ_API_KEY` and `TAVILY_API_KEY` in your environment (`~/.bashrc`). Only external dependency: `python3-requests`.

## Models

| Key | Model | Use case |
|-----|-------|----------|
| Auto | *(routed per message)* | Default |
| 1 | `llama-3.1-8b-instant` | Fast — casual chat, simple Q&A |
| 2 | `llama-3.3-70b-versatile` | Smart — code, explanations, writing |
| 3 | `deepseek-r1-distill-llama-70b` | Reasoning — math, logic, proofs |

Use `/model` in the terminal or the model selector in the web UI to lock to a specific model. `/model 0` returns to auto-routing.

## TTS

| Language | Detection | Terminal | Web UI |
|----------|-----------|----------|--------|
| English | default | [Piper](https://github.com/rhasspy/piper) `en_US-lessac-medium` | Groq Orpheus `daniel` |
| Spanish | ñ ¿ ¡ accented vowels | Piper `es_MX-ald-medium` | Browser `speechSynthesis` |
| Russian | Cyrillic characters | Piper `ru_RU-dmitri-medium` | Browser `speechSynthesis` |
| Hebrew | Hebrew Unicode block | espeak-ng `-v he` | Browser `speechSynthesis` |

### Installing Piper (terminal TTS)

```bash
# Download binary (aarch64 / Pi Zero 2W)
wget https://github.com/rhasspy/piper/releases/latest/download/piper_linux_aarch64.tar.gz
tar -xzf piper_linux_aarch64.tar.gz -C ~/piper --strip-components=1
ln -sf ~/piper/piper ~/.local/bin/piper

# English voice
wget -P ~/piper https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
wget -P ~/piper https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json

# Spanish voice
wget -P ~/piper https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/ald/medium/es_MX-ald-medium.onnx
wget -P ~/piper https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/ald/medium/es_MX-ald-medium.onnx.json

# Russian voice
wget -P ~/piper https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx
wget -P ~/piper https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json
```

Hebrew uses `espeak-ng` which is available via apt: `sudo apt install espeak-ng`

## Terminal commands

| Command | Action |
|---------|--------|
| `/model [0-3]` | Lock model or return to auto |
| `/tts` | Toggle speech output |
| `/memory` | Show stored facts |
| `/memorize` | Extract facts from this session |
| `/forget-fact N` | Remove fact #N |
| `/clear` | Clear session context |
| `/forget` | Clear session + history |
| `/bt` | Manage Bluetooth audio devices |
| `quit` | Exit (auto-memorizes session) |

## Hardware

Raspberry Pi Zero 2W — 512 MB RAM, 4× ARM Cortex-A53. All inference is on Groq's cloud; the Pi only runs the HTTP server and Piper TTS locally.
