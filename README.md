# Zeev

A personal AI companion running on a **Raspberry Pi Zero 2W**. Zeev uses [Groq](https://groq.com) for fast cloud inference and speaks back in human-quality voices. Supports a terminal REPL, a mobile-friendly web UI, and a standalone push-to-talk device mode via the Whisplay HAT.

## Features

- **Fast chat** — auto-routes each message to the right model (8B fast, 70B smart, DeepSeek R1 for reasoning)
- **Web search** — keyword heuristic triggers [Tavily](https://tavily.com) search and injects results into the reply
- **Persistent memory** — extracts facts about you from conversations and recalls them on every turn
- **History RAG** — keyword-indexes past conversations and injects relevant exchanges into context
- **Torah RAG** — local SQLite FTS5 database of Tanakh, Mishna, Talmud, Apocrypha, Liturgy, Zohar, Dead Sea Scrolls, and Sumerian literature; relevant passages injected into context automatically; scripture replies use an expanded 1,200-token limit so passages are never cut off mid-verse
- **Multilingual TTS** — speaks English, Spanish, Russian, and Hebrew with distinct voices; Hebrew gTTS used whenever Hebrew characters appear in a response
- **Volume control** — adjust system volume from the terminal (`/vol`, `/vol+`, `/vol-`, `/vol N`) or the web UI (`🔉` / `🔊` buttons); works with both standard ALSA and the WM8960 HAT
- **Quantum reasoning** — maps any idea or dilemma to a quantum circuit, simulates interference, and interprets the pattern as insight; compounds daily via `quantum_daily.py` (8 canonical scenarios, cron 6 AM)
- **Music playback** — natural language YouTube search via yt-dlp + ffmpeg (`play some jazz`, `stop`)
- **Bluetooth audio** — pair and connect headphones by voice (`scan for bluetooth`, `pair my headphones`, `disconnect bluetooth`); all TTS and music routes through the headphones when connected; `/bt` slash command for manual control
- **Phone calls (HFP)** — dial and receive calls via Bluetooth HFP; auto-detects voicemail (leaves a message), IVR menus (navigates with DTMF), or live callers (converses naturally); call intent extracted from user input (`call <number> to <reason>`); all call audio routed through the SCO channel with Groq Orpheus → Cartesia (~100ms) → Piper → gTTS fallback chain; Whisper hallucinations on ring tone filtered automatically; greeting deferred until call type is known; **speculative pre-generation** runs during ringing so the voicemail message is ready at the beep; aborts cleanly if phone isn't connected via HFP; live call LLM carries full conversation history across turns for natural topic continuity; live-answer detection prioritizes early speech onset over regex so Whisper hallucinations on 8kHz SCO audio can't misclassify a real pickup as voicemail
- **Quantum conversation scenarios** (`quantum_convo.py`) — runs a quantum circuit over conversation directions (empathy, playfulness, depth, small talk) and uses the interference pattern to generate a prioritized call intent; `python3 zeev/quantum_convo.py --name NAME --call NUMBER`
- **Voice input** — Web Speech Recognition in the browser; Groq Whisper STT in device mode
- **Thermal camera** — MLX90640 32×24 thermal imager; ASCII heatmap in terminal, live canvas in web UI
- **Mobile web UI** — dark, responsive single-page chat with streaming tokens
- **Device mode** — standalone push-to-talk companion on the Whisplay HAT (LCD face, LED, button)
- **GPS / geolocation** — WiFi-triangulated location via Google Geolocation API (10–100m accuracy when `GOOGLE_GEOLOC_KEY` set), beacondb as free fallback, IP geolocation as last resort; reverse-geocoded to city/region via Nominatim; injected into context automatically on location queries; `/gps` terminal command; `GET /gps` web endpoint
- **SQLite storage** — all runtime state (messages, memory facts, notes, settings, quantum insights) in a single WAL-mode `zeev.db`; no flat files

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

**Whisplay HAT device mode:**
```bash
python3 zeev/zeev.py --device
```

Requires `GROQ_API_KEY` and `TAVILY_API_KEY`. Copy `.env.example` to `.env` and fill in your keys — the app loads `.env` automatically. Only external dependency: `python3-requests`.

## Models

| Key | Model | Use case |
|-----|-------|----------|
| Auto | *(routed per message)* | Default |
| 1 | `llama-3.1-8b-instant` | Fast — casual chat, simple Q&A |
| 2 | `llama-3.3-70b-versatile` | Smart — code, explanations, writing |
| 3 | `deepseek-r1-distill-llama-70b` | Reasoning — math, logic, proofs |

Use `/model` in the terminal or the model selector in the web UI to lock to a specific model. `/model 0` returns to auto-routing.

**Rate-limit resilience**: if 70B or R1 hits a Groq 429 (TPM burst or daily TPD limit), Zeev automatically retries with 8B instead of surfacing an error. Cooldowns are tracked per-model so a 70B limit never blocks 8B. In device mode, the Whisplay display shows a specific message ("Rate limited", "No network") and errors are logged to `data/zeev_errors.log`.

## TTS

| Language | Detection | Terminal | Web UI | Device mode |
|----------|-----------|----------|--------|-------------|
| English | default | Piper `en_US-lessac-medium` | Groq Orpheus `daniel` | Groq Orpheus `daniel` |
| Spanish | ñ ¿ ¡ accented vowels | Google Translate TTS + mpg123 | Google Translate TTS + mpg123 | Google Translate TTS + mpg123 |
| Russian | Cyrillic characters | Google Translate TTS + mpg123 | Google Translate TTS + mpg123 | Google Translate TTS + mpg123 |
| Hebrew | any Hebrew Unicode character | Google Translate TTS + mpg123 | Google Translate TTS + mpg123 | Google Translate TTS + mpg123 |

Hebrew gTTS is also forced for Torah/Sefaria query responses even when the reply contains no Hebrew characters. Device mode tries Groq Orpheus first (English only, ~200ms), then Google Translate TTS, then Piper, then espeak-ng.

### Installing Piper (terminal TTS)

```bash
# Download binary (aarch64 / Pi Zero 2W)
wget https://github.com/rhasspy/piper/releases/latest/download/piper_linux_aarch64.tar.gz
tar -xzf piper_linux_aarch64.tar.gz -C ~/piper --strip-components=1
ln -sf ~/piper/piper ~/.local/bin/piper

# English voice
wget -P ~/piper https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
wget -P ~/piper https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

Hebrew, Spanish, and Russian use Google Translate TTS (no install needed). Requires `mpg123`: `sudo apt install mpg123`

## Torah RAG (Sefaria)

Zeev can retrieve passages from a local SQLite FTS5 database spanning Tanakh, Mishna, Talmud, Apocrypha, Liturgy, Zohar, Dead Sea Scrolls, and Sumerian literature. When a query matches known keywords (including "parsha"/"parshah"/"portion"), up to 3 relevant passages are injected into the system prompt before the Groq call. Torah queries automatically use the 70B model with a 1,200-token limit and FTS5 noise-word filtering so common verbs and time references don't pollute passage matching.

**Corpora:**

| Corpus | Content | Size | Time |
|--------|---------|------|------|
| Tanakh | 929 chapters (Torah, Nevi'im, Ketuvim) | ~10 MB | ~5 min |
| Mishna | 525 chapters | ~8 MB | ~3 min |
| Gemara | ~5400 daf-sides (Babylonian Talmud) | ~240 MB | ~45 min |
| Apocrypha | Ben Sira, Tobit, Judith, 1–2 Maccabees, Wisdom of Solomon, Prayer of Manasseh, Psalm 151 | ~2 MB | ~2 min |
| Liturgy | Siddur Ashkenaz (456 sections), Pesach Haggadah, The Jonathan Sacks Haggadah | ~3 MB | ~5 min |
| Zohar | ~1806 chapters (all parshiyot + Idra Rabba/Zuta, Sifra DiTzniuta, Addenda) | ~5 MB | ~15 min |
| DSS | ~11,000 fragments of the Dead Sea Scrolls in Hebrew/Aramaic (ETCBC corpus) | ~5 MB | ~10 min |
| Sumerian | 381 texts from ETCSL (myths, hymns, Gilgamesh, royal praise, lamentations) | ~3 MB | <1 min |

**Building the database** (resume-safe — re-run freely after interruption):
```bash
# Full corpus (~75 min, ~275 MB)
python3 zeev/import_sefaria.py

# Skip Gemara (~30 min, ~35 MB)
python3 zeev/import_sefaria.py --corpus tanakh,mishna,apocrypha,liturgy,zohar,dss,sumerian

# Single corpus
python3 zeev/import_sefaria.py --corpus dss
python3 zeev/import_sefaria.py --corpus sumerian
```

Sefaria corpora are fetched via the [Sefaria public API](https://www.sefaria.org/api/). DSS uses [ETCBC/dss](https://github.com/ETCBC/dss) Text-Fabric files. Sumerian uses [ETCSL](https://etcsl.orinst.ox.ac.uk/) via a GitHub mirror. Already-imported refs are always skipped.

**Notes:**
- 3 Maccabees, 4 Maccabees, Baruch, and Letter of Jeremiah are not available in English on Sefaria.
- Several Ben Sira chapters (17, 22–24, 29, 36) are split into `a`–`g` sub-refs and handled automatically.
- Liturgy sections are fetched as complete units via the Sefaria index tree.
- Zohar chapters with no English translation are marked done and silently skipped.
- DSS is in Hebrew/Aramaic only (no free English translation available). The ETCBC corpus covers ~1,001 scroll sigla with ~11,000 fragments.
- Sumerian is English translation from ETCSL (Oxford), fetched as a single JSON.

## Quantum reasoning

Say `quantum: <your dilemma>` or `run quantum on <idea>` to trigger the pipeline:

1. LLM maps the idea to 2–4 options encoded as qubits with phase angles (low = aligned, high = conflicted)
2. Circuit simulated via Qiskit (if available in `~/qiskit-env`) or a pure-Python statevector fallback
3. LLM interprets the interference pattern as a concrete insight, enriched by past quantum sessions

**Daily teaching:** `python3 zeev/quantum_daily.py` runs one of 8 canonical human-dilemma scenarios (selected by day-of-year) and stores the result in `zeev.db`. Scheduled via cron at 6 AM — each run's interpretation becomes context for future ones.

```
You: quantum: focus deeply on one skill vs explore many things
[quantum] mapping to circuit…
Options: Deep Mastery, Broad Exploration, Flexible Hybrid, Opportunistic Sampling
Interpretation: The circuit strongly favors Broad Exploration + Flexible Hybrid (58%)…
```

## Music playback

Say `play <query>` or `play some <genre>` to search YouTube and stream audio via yt-dlp + ffmpeg + mpg123. Say `stop` to stop playback.

```
You: play some Coltrane
[playing: john coltrane a love supreme]
```

Requires `yt-dlp`, `ffmpeg`, and `mpg123`:
```bash
sudo apt install ffmpeg mpg123
pip3 install yt-dlp
```

## Thermal camera (MLX90640)

Connect the MLX90640 to the **software I2C bus** on GPIO5 (SDA) / GPIO6 (SCL). Add to `/boot/firmware/config.txt`:

```
dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=5,i2c_gpio_scl=6,i2c_gpio_delay_us=10
```

Install dependencies:
```bash
pip3 install adafruit-circuitpython-mlx90640 smbus2
```

Zeev auto-detects the sensor at startup. In the terminal use `/thermal` for a colored ASCII heatmap, or `/thermal <question>` to ask Zeev about what it sees. The web UI shows a 🌡 button that renders a live canvas heatmap.

## Raspberry Pi Camera Module V2 NoIR

Zeev uses the **Camera Module V2 NoIR** (IMX219 sensor, no IR filter) connected via the CSI ribbon cable.

**Enable the camera:**
```bash
# Add to /boot/firmware/config.txt
dtoverlay=imx219
```

**Install picamera2:**
```bash
sudo apt install -y python3-picamera2
```

Zeev auto-detects the camera at startup via `picamera2`. In the terminal use `/look [question]` to take a photo and ask Zeev about it. The web UI shows a 📷 button (with optional flip toggle). Use `/flip` or the ↕ button to rotate the image 180° (saved persistently).

In **device mode** (Whisplay push-to-talk), natural language camera phrases work hands-free — say "what do you see", "take a photo", or "can you see anything" and Zeev captures a JPEG and describes it via the Llama 4 Scout vision model.

---

## Whisplay HAT device mode

The [PiSugar Whisplay HAT](https://github.com/PiSugar/Whisplay) adds a 1.96" ST7789 LCD (240×280), WM8960 audio codec (mic + speaker), RGB LED, and a push button on GPIO17.

**Install the driver (once, requires reboot):**
```bash
git clone https://github.com/PiSugar/Whisplay.git ~/Whisplay
cd ~/Whisplay && sudo bash install_driver.sh && sudo reboot
```

**Install Python dependencies:**
```bash
sudo apt install -y python3-pillow
```

**Run device mode:**
```bash
python3 zeev/zeev.py --device
```

Hold the KEY button to record, release to send. Press again while Zeev is speaking to interrupt. The LCD shows an animated face that reflects the current state (idle / listening / thinking / speaking). Speaker volume is set to raw 110 (~87%) via `amixer` at startup.

**Audio device:** WM8960 appears as `hw:wm8960soundcard`. Recording uses `arecord -f S16_LE -r 16000 -c 1 plughw:wm8960soundcard,0`; playback uses `aplay` or `mpg123 -a plughw:wm8960soundcard,0`.

**TTS priority in device mode:**
1. Groq Orpheus (cloud, English only, ~200ms)
2. Google Translate TTS + mpg123 (he/es/ru, ~500ms)
3. Piper local (English fallback) — retried once on process crash before falling through
4. espeak-ng (last resort)

**Bluetooth audio** (requires `sudo apt install bluez-alsa-utils libasound2-plugin-bluez`):

Say `scan for bluetooth` → Zeev scans 10s and lists nearby devices. Say the device name (or `pair it` if only one) → Zeev pairs, trusts, and connects. All TTS and music then routes through the headphones automatically, resampled via ffmpeg to match the A2DP negotiated format. Say `disconnect bluetooth` to revert to the speaker. Manual control via `/bt scan`, `/bt pair <N>`, `/bt <N>`, `/bt off`.

At startup, if headphones are already connected, Zeev auto-detects them via `bluealsa-aplay --list-pcms` and sets BT volume to raw 50/127 (~39%). Speaker volume is always set to raw 110/127 (~87%) regardless of BT state.

Physical disconnects (headphones powered off or out of range) are handled automatically: before each TTS call Zeev re-checks `bluealsa-aplay --list-pcms` and falls back to the wired speaker if the BT device is no longer present.

## Terminal commands

| Command | Action |
|---------|--------|
| `/model [0-3]` | Lock model or return to auto |
| `/tts` | Toggle speech output (on by default) |
| `/vol` | Show current volume level |
| `/vol+` or `/vol up` | Raise volume by 10% |
| `/vol-` or `/vol down` | Lower volume by 10% |
| `/vol N` | Set volume to N% (0–100) |
| `/look [question]` | Take a photo and ask Zeev about it |
| `/thermal [question]` | Capture thermal frame; optionally ask Zeev |
| `/memory` | Show stored facts |
| `/memorize` | Extract facts from this session |
| `/forget-fact N` | Remove fact #N |
| `/note <text>` | Save a persistent note |
| `/notes` | List all notes |
| `/forget-note N` | Remove note #N |
| `/clear` | Clear session context |
| `/forget` | Clear session + history |
| `play <query>` | Play YouTube audio |
| `/stop` | Stop music playback |
| `/bt` | Manage Bluetooth audio devices |
| `/gps` | Show current location (WiFi-triangulated or IP) |
| `quit` | Exit (auto-memorizes session) |

## Hardware

Raspberry Pi Zero 2W — 512 MB RAM, 4× ARM Cortex-A53. All LLM inference and TTS (device mode) run on Groq's cloud; the Pi handles the HTTP server, local Piper TTS, and sensor I/O.
