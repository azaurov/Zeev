---
name: run-zeev
description: Build, launch, and drive Zeev's web mode (the only part of this app that runs headless off the Pi hardware) — start the server, hit its HTTP/SSE endpoints with curl, screenshot the chat UI. Use when asked to run Zeev, start the web server, test the chat endpoint, or screenshot the web UI. Device mode (--device, the Whisplay LCD/mic/speaker HAT) and --call (Bluetooth SCO) cannot run off the Pi and are out of scope for this skill.
---

# Running Zeev (web mode)

Zeev is a single-file app (`zeev/zeev.py`) with four run modes: terminal
REPL, `--web`/`--https` (stdlib `ThreadingHTTPServer`, no framework), and
`--device` (Whisplay HAT — Pi-only, not coverable here). **Web mode is
the only mode drivable off the Pi**, and it's what this skill targets.

All paths below are relative to the repo root (`/home/azaurov/Zeev`).

## Prerequisites

- Python 3 with `requests` installed (stdlib + `requests` only, per
  `CLAUDE.md`).
- `.env` at repo root with at least `GROQ_API_KEY` and `TAVILY_API_KEY`
  (loaded by zeev.py's own plain-text parser — not python-dotenv). Without
  these, `/chat` will fail LLM calls but the server still starts and every
  other endpoint above still works.
- `zeev/data/zeev.db` (SQLite) — created automatically on first run if
  missing.

No build step — it's plain Python, nothing to compile.

## Run (agent path) — use the driver, not `--web` directly

`python3 zeev/zeev.py --web` binds **port 5000**, which is frequently
already taken on this box by an unrelated production Flask app (the
sogdiana-gematria.net site) — confirmed live: it returns a Werkzeug 401,
not a "port in use" error, so it looks like Zeev almost worked and silently
isn't. Use the committed driver instead, which imports `zeev.py` as a
module (safe — every side effect in the file is gated behind
`if __name__ == "__main__":`) and starts `run_web_server()` on a free port
you choose:

```bash
python3 .claude/skills/run-zeev/driver.py --port 5057 &
sleep 2   # let the ThreadingHTTPServer bind
curl -s http://127.0.0.1:5057/    # confirm it's up — should return the chat HTML
```

Then drive it with curl:

```bash
# Chat — Server-Sent Events stream (model choice, then token-by-token reply)
curl -s -N -X POST http://127.0.0.1:5057/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Say hello in exactly three words."}'
# -> data: {"model": "GPT-OSS-20B"}   (model label — re-verify if it drifts again,
#                                      it's read from the live Groq model table)
#    data: {"token": "Hello"}  data: {"token": ","}  ...  data: [DONE]

# Memory facts
curl -s http://127.0.0.1:5057/memory        # {"facts": [...]}

# Volume
curl -s http://127.0.0.1:5057/volume        # {"volume": 70}
curl -s -X POST http://127.0.0.1:5057/volume -d '{"volume":50}' \
  -H "Content-Type: application/json"
# -> 200 {"volume": 70} -- POST is a graceful no-op off-Pi (no ALSA device to
#    set), same "no hardware, no error" pattern as /health and /battery below.
#    Don't expect the value to actually change in this environment.

# Health / battery / thermal / notes / gps — all return graceful nulls/empty
# off the Pi, not errors, since there's no hardware to read:
curl -s http://127.0.0.1:5057/health        # {"temp_c": null, "load1": ..., "warnings": []}
curl -s http://127.0.0.1:5057/battery       # {"level": null, "charging": null}
curl -s http://127.0.0.1:5057/thermal-status  # {"available": false}
curl -s http://127.0.0.1:5057/notes         # {"notes": []}

# Screenshot the chat UI (headless Chrome, already on this box):
google-chrome --headless --disable-gpu --no-sandbox \
  --screenshot=/tmp/zeev_web_screenshot.png --window-size=1280,900 \
  http://127.0.0.1:5057/
```

Stop the server with `kill %1` (or `pkill -f 'driver.py --port 5057'`).

## Run (human path)

```bash
python3 zeev/zeev.py --web       # binds 0.0.0.0:5000 — check it's free first
python3 zeev/zeev.py --https     # binds 0.0.0.0:5443, needs cert.pem/key.pem in zeev/data/
```
Open the printed URL in a browser. Useless in a headless container — use
the driver above instead.

## Direct invocation (for PRs touching internal logic, not the HTTP layer)

Most of the interesting logic (routing, RAG, gates, prompt assembly) is in
module-level functions, importable without starting any server:

```bash
python3 -c "
import sys; sys.path.insert(0, 'zeev')
import zeev
print(zeev.route_model('what is 47 * 12?'))   # e.g. routing decision
print(zeev.needs_torah('tell me about the Talmud'))
"
```

`zeev/test_sqlite_migration.py` and `zeev/test_call_personas.py` look like
tests but are **standalone scripts**, not pytest — `pytest.ini` explicitly
excludes them (`test_call_personas.py` places a real phone call). Don't run
them as part of a suite.

## Test

```bash
python3 -m pytest tests/ -q
```

1118 tests as of this writing (was 989 — re-verify count with
`python3 -m pytest tests/ -q --collect-only` rather than trusting this
number long-term). **This is slow — budget 5+ minutes, not the
usual pytest jog.** It imports the full `zeev.py` (heavy top-level module,
many mocked subsystems) once per collection and exercises large gated
logic trees (call flows, memory extraction, RAG reranking, etc.) with real
assertions, not just smoke checks. Don't assume a stall at the 60-120s mark
means it hung — let it run.

## Gotchas

- **Port 5000 collision looks like success, not failure.** Confirmed live:
  hitting `/` on a stray unrelated Flask app on 5000 returns HTTP 401 with
  a `WWW-Authenticate: Basic` header — plausible-looking output that has
  nothing to do with Zeev. Always confirm the port is actually Zeev's by
  checking for the `<title>Zeev</title>` HTML, not just a 2xx/4xx status.
- **`nohup ... > /tmp/foo.log &` output files vanish between tool calls in
  this environment**, even though the backgrounded process itself keeps
  running (confirmed: `ps` still showed the pid after the log file
  disappeared). Write driver/test logs into the session scratchpad
  directory instead of bare `/tmp`, or just don't redirect at all and use
  `run_in_background`/`Monitor`-style waiting if your harness supports it.
- **`import zeev` is safe** — despite being a 15,000+ line single-file app
  with module-level Groq/Torah/RAG setup, all process-starting side effects
  (web server, device mode, terminal REPL, outbound call) are gated behind
  `if __name__ == "__main__":` at the very bottom of the file. Importing it
  only defines functions/classes and loads `.env` — no server starts, no
  hardware is touched.
- **`/transcribe` and `/snap` need real audio/camera input** to do anything
  useful, but they fail differently — `/transcribe` with no body returns a
  clean `400 {"error": "no audio"}`; `/snap` with no body returns `200` with
  an SSE stream (`data: {"error": "Camera not available"}` then
  `data: [DONE]`), not a 400. Both are good enough to confirm routing
  without hardware, just check status code vs. stream shape correctly.
- **Device mode (`--device`) cannot be launched here at all** — it imports
  Whisplay HAT display/GPIO/I2C libraries at module load inside
  `run_device_mode`, which don't exist off the Pi. Don't attempt it; this
  skill only covers `--web`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `curl` to `/` returns `{"error":"Unauthorized"}` / 401 with `WWW-Authenticate: Basic` | That's a different app already on that port (confirmed: an unrelated production Flask site on 5000 on this box). Pick a different `--port` for the driver. |
| `/chat` SSE stream returns only a `data: {"model": ...}` line and then nothing for a long time | Normal on a cold Groq/OpenRouter connection — first token can take several seconds. Give it 30-40s before assuming it's stuck (confirmed live: a 20s curl timeout cut off a stream that completed fine at 40s). |
| Driver won't bind, `OSError: [Errno 98] Address already in use` | Something (maybe your own previous driver run) is still holding the port — `pkill -f 'driver.py --port <N>'` or pick a new port. |
