# CLAUDE.md archive — full incident write-ups

Verbatim originals of sections that were compressed in CLAUDE.md on 2026-09-18 to bring it
under its 120,000-character limit (it had reached ~136k). Nothing here was deleted or
rewritten; CLAUDE.md keeps every non-obvious constraint in short form and points here for
the incident trail (dates, live-debugging narrative, measurements, rejected alternatives).

**Read the matching section here before changing the code it describes** — the compressed
version tells you *what* not to break, this tells you *why* and what was already tried.

Each section below is exactly as it stood in CLAUDE.md at commit e521f94 (the dead-end and yard-speaker additions made afterwards live only in CLAUDE.md).

---

### Google Voice calling — no phone/SIM involved, two backends (`--via gv` / `--via c11`)

Three `via` options for `run_call_mode`/`python3 zeev/zeev.py --call
NUMBER --via {sco,gv,c11}`. `via="sco"` (the default) is completely
unaffected by either of the below — every existing call site still passes
`call_io=None`, which builds `SCOCallIO(mac)` exactly as before.

**`--via gv`**: places a genuine Google Voice VoIP call via
`voice.google.com` in a real Chrome browser, entirely independent of any
phone/SIM/carrier. **Only runs on bosgame as of this writing** — the
Chrome instance and its virtual audio devices live there, not on the Pi;
there is no Pi↔bosgame relay yet (deferred, see below).

**`--via c11`** (recommended over `--via gv` — see the comparison at the
end of this section): also a genuine no-SIM Google Voice VoIP call, but
placed by **C11** (a dedicated WiFi-only, no-telephony Android device —
see [[dogcaller_vm_bosgame]]-adjacent memory for its other role in the
Wyze camera rig) instead of a browser, and carried over the **same
SCO/HFP audio path `--via sco` already uses in production** — zero new
audio backend, `call_io = SCOCallIO(C11_BT_MAC)`, same as the S22. C11
places the call itself via its own Google Voice app
(`c11_gv_dial()` — an adb-driven `ACTION_CALL` intent into
`AndroidCallIntentActivity`, since C11 has no modem for the normal
`bt_call_dial()`'s `ATD` AT-command path to talk to). Needs
`C11_ADB_SERIAL` in `.env` (C11's current wireless-debugging `IP:port` —
**rotates on every C11 reboot**, Android's own security behavior, not
fixable without rooting the device; must be refreshed by hand each time,
check C11's own Wireless Debugging screen) and `C11_BT_MAC` (defaults to
the known value, override only if C11 is ever re-paired under a different
adapter). Live-verified 2026-09-11, twice, bidirectionally, after fixing
a crashed Bluetooth HAL on C11 (`adb logcat` caught a real `SIGSEGV` in
`android.hardware.bluetooth@1.0-service` — fixed by `adb reboot`, not a
code issue) — real captured speech (RMS up to 2690/32767) and a real Zeev
TTS line the user confirmed hearing "loud and clear." **Always hang up
explicitly when testing this path** — a forgotten hangup leaves GV's
app-internal state stuck ("You're already in a call" on the next dial,
confirmed live) even though `dumpsys telecom`'s `mCalls:` shows nothing
(that field is NOT a reliable liveness check for this call type — it read
empty through two separate genuinely-active calls); check the app's own
UI/notification banner instead, or just always call `hangup()`.
- **`AT+CHUP` (the standard HFP hangup command, what `SCOCallIO.hangup()`
  uses) is accepted by C11's Bluetooth stack and reports "OK" — but does
  NOT actually end a Google Voice SelfManaged VoIP call.** Found live
  wiring `--via c11` into `run_call_mode`: sent `AT+CHUP`, got `OK` back,
  screenshotted C11 a moment later and the call was still visibly active,
  timer still counting. A real Android platform gap (AT-command call
  control doesn't reliably bridge to a third-party app's SelfManaged
  call), not a bug in how the command was sent. **Fixed with `C11CallIO`**
  (`SCOCallIO` subclass, one method overridden): `hangup()` sends a
  `KEYCODE_ENDCALL` adb keyevent instead — Android's own global "end call"
  signal, confirmed live to actually work (screen showed "Ending…" then
  "Call ended"). Everything else (`speak`/`capture_popen`/`fast_detect`/
  `is_hungup`/`send_dtmf`/`play_wav`) is inherited from `SCOCallIO`
  unchanged. `run_call_mode`'s `via == "c11"` branch builds `C11CallIO`,
  not `SCOCallIO`, and its own dropped-link abort path calls
  `c11_gv_hangup()` directly (no `call_io` object exists yet at that
  point).
  - **Not yet confirmed whether `is_hungup()` (inherited, `AT+CLCC`-based)
    has the same reliability gap mid-conversation** — it did produce one
    false "hung up" reading moments after dialing (most likely a startup
    race in how quickly the RFCOMM/AT-command channel stabilizes after
    C11 places a VoIP call — the SCO *audio* channel itself was
    confirmed genuinely fine at that same moment via direct `arecord`).
    Not fixed since it wasn't reproduced as an ongoing problem; flag this
    if a future call seems to end itself unexpectedly mid-conversation.
  - **`c11_ensure_bt_connected()` also had a real live bug**: it
    unconditionally ran `bluetoothctl connect <mac>` before checking
    whether C11 was already connected — found live that this doesn't
    error quickly on an already-connected device, it just hangs for the
    full subprocess timeout (burned the whole 15s budget this way while
    C11 was already fine the entire time, confirmed via
    `bluealsa-aplay -l`). Fixed: checks `bt_hfp_detect()` first and skips
    the `bluetoothctl connect` call entirely if C11's already connected —
    the overwhelmingly common case in practice (C11 stays connected
    across calls unless it reboots).
- `tests/test_c11_call.py` pins the pure-logic pieces (number formatting,
  missing-serial handling, the `C11CallIO`/`SCOCallIO` method-override
  shape) — no live adb/Bluetooth in the test suite.

- **Why an emulator wasn't used first**: a same-night investigation tried
  routing this through the real Google Voice *Android app* on
  `dogcaller-vm` (the same bosgame Android VM the Wyze camera automation
  uses). Proven dead end via root-level `strace` evidence: the emulator's
  qemu audio backend never even attempts a pulse connection before
  failing, and separately, the AVD's audio *capture* path is broken for
  all sources while playback works — neither is fixable by config. Full
  writeup in project memory `gv_vm_calling_investigation.md`. The browser
  approach below is unrelated to that dead end and doesn't share its
  problems (ordinary WebRTC on bosgame's already-working PipeWire stack).
- **Audio routing**: two persistent virtual PipeWire sinks, defined in
  `~/.config/pipewire/pipewire-pulse.conf.d/99-dogcaller.conf` on bosgame
  (survive a pipewire-pulse restart, unlike an ad-hoc `pactl load-module`)
  — `dogcaller_call_out` (Chrome's selected speaker; the call's incoming
  audio lands here, captured via `parec --device=dogcaller_call_out.monitor`)
  and `dogcaller_mic_feed` (Chrome's selected microphone; `paplay
  --device=dogcaller_mic_feed` is what makes the far end hear Zeev speak).
  Chrome is launched with `PULSE_SINK`/`PULSE_SOURCE` env vars pinning it
  to these — **load-bearing**: bosgame's real default sink is a Bluetooth
  yard speaker (`bluez_output.*`, see `docs`/memory on the M400B yard
  speaker), and a Chrome instance launched without this pin would play
  call audio there instead. `gv_call.py`'s `ensure_virtual_sinks()` refuses
  to launch Chrome at all if the sinks aren't enumerable yet, rather than
  risk libpulse's silent fallback to the default sink.
- **`zeev/gv_call.py`** — `GVSession` (Chrome/CDP lifecycle: launch or
  reuse via a fixed debug port, persistent profile at
  `~/.config/zeev-gv-chrome` so login survives a relaunch, `dial()`/
  `state()`/`hangup()` via DOM manipulation) and `GVCallIO` (the
  `bt_call_loop` backend adapter). Needs the `websocket-client` pip
  package (imported defensively — raises a clear `GVCallError`, doesn't
  crash at import, if missing). `--use-fake-ui-for-media-stream` on the
  Chrome launch is load-bearing: without it, a fresh (non-logged-in-yet)
  launch blocks forever on an unattended mic-permission dialog — it still
  uses the real pinned devices, it just skips the interactive prompt.
- **React-controlled input gotcha**: `voice.google.com`'s dial-pad input is
  a React controlled component — setting `.value = ...` directly does
  nothing; `dial()` goes through the native `HTMLInputElement` value
  setter (`Object.getOwnPropertyDescriptor(...).set.call(input, digits)`)
  and then dispatches a real `input` event so React's `onChange` actually
  fires. Found live; would otherwise look like the number was entered
  (DOM shows it) while the Call button stays inert.
- **`bt_speak_sco` gained one param, `play_cmd`** — the entire multi-engine
  TTS chain (Orpheus → Cartesia → Piper → gTTS / ElevenLabs for
  non-English) is unchanged and shared by both backends; only the final
  playback subprocess command is now overridable (defaults to the
  original `aplay -D sco_dev ...` when not given). The one piece of that
  chain that does NOT honor `play_cmd`: the zeev-audio Go daemon's own
  `sco_speak()` shortcut, which plays straight to a real SCO device itself
  — explicitly skipped when `play_cmd is not None` so it can't silently
  ignore a non-SCO backend's playback target.
- **`_vad_collect()` (turn>0 caller capture) needed no changes at all** —
  it already just reads raw S16LE frames off any `Popen.stdout`, built for
  `arecord` but equally happy fed by `parec --device=dogcaller_call_out.monitor
  --raw --format=s16le --rate=16000 --channels=1` (`GVCallIO.capture_popen()`).
- **Known gaps, deliberately deferred, not silently unhandled**:
  - `GVCallIO.send_dtmf()` returns `False` + logs — IVR navigation through
    Google Voice's web UI is unverified, not yet attempted.
  - `GVCallIO.fast_detect()` is an MVP: plain capture, no reimplementation
    of `bt_fast_detect`'s SCO-specific onset-heuristic/early-exit
    optimization. Turn 0 falls through to `bt_call_loop`'s existing
    LLM-based call-type classification, same as a genuine `bt_fast_detect`
    miss would.
  - No Pi↔bosgame HTTP relay yet (would mirror `dog_caller_server.py`/
    `DOG_CALLER_URL` — a `gv_call_server.py` + thin HTTP client backend on
    the Pi side). Until built, `--via gv` only works run directly on
    bosgame.
- Live-verified 2026-09-11: three real Google Voice calls placed by hand
  (predating this code — the manual CDP spike this module formalizes),
  including a real Zeev TTS line heard "loud and clear" by a human on the
  far end. The actual `bt_call_loop`/`GVCallIO` code path itself (this
  section) has compile/unit-test coverage (`tests/test_gv_call.py`) but
  not yet its own live end-to-end call — that's the next verification
  step, not yet done as of this writing.
- **`--via gv` vs `--via c11`, which to use**: `c11` is the more reliable
  of the two — it reuses `SCOCallIO` completely unchanged (no new backend
  class, no DOM-scraping-based call-state detection, no browser at all),
  the exact same hardware-level "is a call actually connected" signal
  (BlueZ/BlueALSA) the S22 path already relies on in production. `gv`'s
  own state-detection (`GVSession.state()`) needed three separate
  iterations to get right (see the memory doc — two false-positive
  signals found live before landing on the real one), and its `fast_detect()`
  is a known-weaker MVP. Prefer `c11` unless C11 itself is unreachable
  (e.g. mid-reboot, needing a fresh `C11_ADB_SERIAL`).
- Full incident history/investigation trail, both backends: project
  memory `gv_vm_calling_investigation.md`.


---

### World news ("the shpeel")

"Give me the shpeel" (also "spiel", "world news briefing", "news roundup") gets Alex a curated cross-region digest biased toward stories that wouldn't already be all over mainstream US/UK headlines — `zeev/world_news.py` (shared query list/prompt), `zeev/news_digest.py` (cron job that builds and caches it), `zeev/news_probe.py` (faithfulness grader), plus `get_shpeel()`/`_SHPEEL_RE` in `zeev.py` wiring it into device mode, terminal, and web `/chat`.

- **Cache-first, not live-per-request.** `news_digest.py` runs `world_news.NEWS_QUERIES` (8 curated region/topic Tavily searches — Central Asia, West Africa, Caucasus, Pacific Islands, SE Asia, Balkans, Latin America, plus a generic "overlooked" query) through an LLM summarizer and stores the result + the raw snippets in the `world_news` table. `get_shpeel()` reads the latest row if under `_SHPEEL_MAX_AGE_S` (8h); only when stale/missing does it fall through to a smaller synchronous live pull (4 of the 8 queries, to bound latency inside a real turn).
- **`_SHPEEL_RE` excludes `_TOOL_INTENT_RE`** at all three call sites — "remind me to check the world news briefing tonight" matches `news (roundup|briefing|update)` and would otherwise swallow the reminder before it ever reaches the tool round trip, the same failure class as the goodnight gate and `resolve_subject()`.
- **Each Tavily result is truncated to 1200 chars before concatenation** (`world_news._MAX_RESULT_CHARS`) — the first live cron run concatenated 8 queries × 5 full-length results into a ~30-40KB prompt and got a 413 Payload Too Large from Groq, while bosgame just timed out trying to process the same oversized prompt.
- **`reasoning_effort: "low"` is required on every Groq call both scripts make** (`news_digest.py`'s summarizer, `news_probe.py`'s grader) — found live 2026-08-18, same night as the 413 fix. Two things compounded: (1) `qwen/qwen3.6-27b` (matching `weekly_reflection.py`'s Groq fallback choice) inlines its `<think>` reasoning into `content`, and at this account's default reasoning effort it burned through 500, then 1500, then 3000 completion tokens *still reasoning* across the 8-region synthesis task without ever finishing — a further increase wasn't viable either, since the account's qwen3.6-27b quota caps at 8000 TPM and the prompt alone is already ~2700-3000 tokens. Switched to `openai/gpt-oss-20b`, which tracks reasoning separately from `content` (`usage.completion_tokens_details.reasoning_tokens`) rather than inlining it — but (2) that reasoning is still drawn from the *same* `max_tokens` budget (same class of bug CLAUDE.md's Groq models table already documents for gpt-oss's device-chat fast tier), and this project's cross-region synthesis/grounding-check tasks are dense enough (10-13KB prompts) that even 1200-3000 tokens of budget was consumed entirely by reasoning with zero visible output. Setting `reasoning_effort: "low"` on the request dropped reasoning from 1200+ tokens to 46-258 and both calls finished naturally (`finish_reason: "stop"`) on the very same prompts that previously failed. `world_news.strip_think_text()` (shared, used by both scripts plus zeev.py's live-fallback `_llm_complete`-adjacent call) is kept as defense in depth regardless, since a future model swap could reintroduce inlined reasoning.
- **`world_news.summarize()` treats an empty-after-stripping result as a truncation error, not a successful terse digest** — the live sequence above (before the `reasoning_effort` fix was found) produced exactly this: `strip_think_text` correctly reduced an all-reasoning, no-content reply to `""`, and the pre-fix code stored that empty string as a "successful" digest. Caught and reproduced with `tests/test_world_news_module.py`'s `test_summarize_treats_empty_after_strip_as_truncation_error`.
- **`world_news` gained a `snippets` column after it already shipped** (both `news_digest.py`'s `_open_db()` and `zeev.py`'s `_db()` carry an `ALTER TABLE ... ADD COLUMN` guard, try/except on `sqlite3.OperationalError`, matching this project's usual "no ALTER TABLE" preference only where a fresh table would otherwise be the alternative) — `news_probe.py`'s faithfulness grader needs the *exact* source text a given digest was built from, not a fresh re-fetch (news changes between the digest run and any later grading run).
- **`news_probe.py`** — same shape as `rag_probe.py`: grades a stored digest's summary against its own stored snippets via a fresh-context LLM call, logs to `news_probes` (`digest_id`, `grounded`, `grader_note`), `--report` shows the rolling grounded rate. First real run (once the reasoning-effort fix landed) graded GROUNDED 1/1; over the next ~48 runs the rate settled at 37 grounded / 9 ungrounded / 2 null (~80%).
- **All 9 UNGROUNDED digests investigated in full (found live 2026-09-02, `/test-and-fix`, initial pass + a deeper chase against the raw snippets) split into 2 grader false positives and 7 real fabrications across four distinct sub-patterns, not one.** Checking each flagged claim directly against its stored `snippets` (not just trusting the grader note) mattered: two "fabrications" (a Dwayne Johnson NZ-rugby documentary claim; an El Salvador "Días Eternos" imprisoned-women claim) were actually present verbatim-ish in the source and the grader missed them — real grader-side misses, not model errors, same class as `rag_probe.py`'s already-fixed "grader misjudged a verbatim quote as unsupported" finding. The 7 real fabrications:
  - **Outcome/detail invention** (2 digests) — a Kazakhstan referendum's real "a vote was held" became a fabricated "passed with a narrow/slim majority" in two separate weekly digests.
  - **Cross-snippet conflation** (2 digests) — a real snowfall event near Sarajevo, Bosnia got relabeled as hitting "Albania's capital" (Albania only appeared in an unrelated nearby headline) with invented power outages; a real Cyclone Maila impact on Bougainville got merged with a *different* storm's real threat to Guam into one fabricated "Cyclone Maila slammed Guam."
  - **Severity escalation** (1 digest) — a real headline "Badakhshan Fighting Raises Risks for Tajikistan and Uzbekistan" (a hedge) was rewritten as "fighting... is spilling over into Uzbekistan" (stated as fact).
  - **Fabrication from incidental content** (2 digests) — a Tongan PM who died in 2019 was named as still in office with zero basis in any snippet; a submarine "surfacing off the Solomon Islands" was invented wholesale from an unrelated stock-photo caption of a Chinese submarine elsewhere in the same batch.

  **Fixed both scripts.** `SHPEEL_PROMPT` (`world_news.py`) gained three more paragraphs beyond the original anti-fabrication one: keep each story's facts attached to the right place/event/person and never merge two similar stories (targets conflation), preserve a snippet's hedge/certainty language rather than upgrading a risk into a fact (targets escalation), and treat a photo caption as not itself a news event (targets incidental-content fabrication). `news_probe.py`'s `_GRADE_PROMPT` gained the same "check for an exact or near-exact match before flagging" instruction `rag_probe.py`'s grader already has — CLAUDE.md called them "same shape" but this specific fix had never actually been carried over. No dedicated test for either (grading a hypothetical LLM response isn't something a unit test can verify — same acceptance as the Torah-grounding/confabulation-mitigation fixes); watch the next several `news_probe` runs for whether the four fabrication sub-patterns above stop recurring and whether the false-positive rate drops.
- **The 2026-09-02 conflation guard wasn't specific enough to catch a cross-header misattribution (found live 2026-09-10 via `/news-probe`).** A digest's Latin America paragraph stated a cease-fire-extension claim that was verbatim from a snippet found under the "### Balkans" query header (an unrelated Middle East wire story that happened to rank for that search) and separately fused two distinct, unrelated headlines within the Latin America snippet block ("Is a U.S. military occupation in Latin America inevitable?" and "Record global surge in gas-fired power driven by AI demands") into one fabricated causal claim ("military occupation... amid rising gas demand driven by AI demands"). The existing "keep facts attached to the right place" paragraph didn't anticipate a story crossing query-section boundaries, only detail-bleed between adjacent stories within one region. `SHPEEL_PROMPT` gained two more paragraphs: attribute a snippet to its own actual subject, never to the "### <region>" header it happened to rank under, and never imply a causal/thematic link between two adjacent headlines in the same block unless the source text itself states one. No dedicated test (same reasoning as the paragraphs above); watch subsequent `news_probe` runs for recurrence of either sub-pattern.
- **Two systemd timers**, staggered off the existing quantum-daily/rag-probe/weekly-reflection cluster: `zeev-news-digest.timer` (every 6h at :15 past 00/06/12/18) and `zeev-news-probe.timer` (same cadence, :35 past, ~20min after the digest so a fresh one gets graded soon after). Both `Persistent=true`. See `.claude/skills/add-zeev-timer/` for the general pattern this followed, and `.claude/skills/news-probe/` for running the grader and reading its results.


---

### Named subjects ("check on Smokey")

A pet or person Zeev can be asked about **by name**, sweeping cameras until it finds them. `ZEEV_SUBJECTS=smokey|smoky|smokie:cat:basement-cam|upstairs` (comma-separated entries, `name[|alias…]:kind[:cam|cam]`, same unquoted shape as `OWW_VOICE_MAP`). Cameras default to those with a direct RTSP URL, capped at `ZEEV_SUBJECT_MAX_CAMS` (3). **`.env` is not in git**, so this line has to be added on the Pi itself — without it `WYZE_SUBJECTS` is `{}`, the gate never matches, and the turn falls through to the LLM, which confidently says it can't see Smokey.

- **Name aliases exist because Whisper spells names however it hears them** (the same hazard `_WYZE_CAM_RE`'s comment records for room phrasing). A missed alias fails *silently* — first alias is the one Zeev speaks.

- **`kind` is what the vision model is asked about, never the name.** "Is there a cat in this image" is judgeable; "where's Smokey" invites the model to narrate a shadow as a resting cat — it cannot know which cat is Smokey and will not say so. The name is substituted back into Zeev's reply.
- **The branch sits above the tool branch, so `resolve_subject()` rejects `_TOOL_INTENT_RE` phrasing outright.** "Remind me to check on Smokey at four" is a reminder; without the guard the camera sweep swallows it. Trigger must also appear in the first 60 chars with the name within 40 after it (the `_bt_call_match` shape) — a bare name mid-sentence is far too common.
- **`parse_subject_sighting()` is three-state: yes / no / `None`.** Free-tier vision ignores the `FOUND:` format routinely. Folding unparseable into "no" burns the next camera and then denies the sighting while holding the description that made it; `None` reports the description as uncertain instead.
- **Camera list must not default to all of `WYZE_CAMERAS`** — six of eight never answer, so an unlisted default spends `WYZE_SNAP_TIMEOUT` on each before speaking.
- **Next grab starts under the current vision call** (grab 4–8s vs vision ~21–25s), so a two-camera sweep is ~38s rather than ~58s. Wasted work on a hit is one background ffmpeg.
- A miss is worded **"I didn't see Smokey on …"**, never "he isn't there" — a small model missing a dark cat on a dark couch is the wrong-city failure class. Zero frames reports the cameras as asleep/offline instead, which is a different answer.
- Speaking *through* a camera is **not possible**: the RTSP firmware is outbound-only (no ONVIF backchannel; v3 isn't ONVIF), and docker-wyze-bridge closed audio-out as `wontfix`. **This is exactly why `call_dog_remote()` (see `zeev.py`) doesn't try** — as of 2026-09-08 it plays through a standalone Bluetooth speaker in the yard instead (`~/troubleshooting/wyze-dog-caller`'s `yard_speaker.py`, reached via `DOG_CALLER_URL/call`), not through any camera. Unrelated to the RTSP/subject-sweep cameras this section is about.
- **The M400B yard speaker's keepalive ping was a silent no-op for its entire existence** (found live 2026-09-10, checking whether the speaker auto-reconnects on bosgame's boot). Two separate systemd units on bosgame manage the link: `yard-speaker-connect.service` (boot-time connect, `WantedBy=multi-user.target`) and `yard-speaker-ping.timer`/`.service` (keepalive ping every ~2 min). The connect service's unit file correctly sets `Environment=XDG_RUNTIME_DIR=/run/user/1000`, but the ping service's did not — without it, `yard_speaker.py`'s `_sink_exists()` check (`pactl list sinks short`) can't reach azaurov's PipeWire user session, always returns `False`, and every single ping logged `SKIPPED: sink not present` even while the BT link and the PipeWire sink (`bluez_output.F4:4E:FD:86:8E:F8`) were both genuinely up. **Fixed** by adding the same `Environment=XDG_RUNTIME_DIR=/run/user/1000` line to `/etc/systemd/system/yard-speaker-ping.service` on bosgame (outside the repo) — verified live, `OK: ping sent` on the next tick after `daemon-reload`. **`yard-speaker-connect.service` (`WantedBy=multi-user.target`) had never actually run through a real reboot as of 2026-09-10** — checked via `journalctl --list-boots`: bosgame's last real boot at that point was 2026-09-10 00:00:20 with a clean `bluetooth.service` start at 00:00:31 and no connect attempt logged until 00:48, which turned out to be the service's *first-ever install* (`cp` to `/etc/systemd/system/` + `daemon-reload` + `enable` + a manual `systemctl start`), not a boot trigger. That manual run did fail for a real reason (`bluetoothd: Unable to get Hands-Free Voice gateway SDP record: Host is down` — the M400B was genuinely unreachable at that moment). **Verified on an actual reboot the same night** (23:52:27, requested specifically to test this): `yard-speaker-connect.service` started at 23:52:38 (11s post-boot, purely `multi-user.target`-triggered — confirmed independent of any user login: it started before session '1' opened for azaurov at 23:52:45, and the real GDM login at 23:53:01 landed in the middle of the connect attempt, not before or after it) and logged `OK: M400B connected` at 23:53:07. Boot-time auto-reconnect is now confirmed working end-to-end.
- **The keepalive ping interval (2 min) was needlessly aggressive for a solar-powered outdoor speaker** (found live 2026-09-10/11, user flagged overnight battery drain). `yard_speaker.py`'s `ping()` docstring already explained *why* the keepalive exists (the M400B has its own idle-disconnect timeout independent of BlueZ's link state, and letting it lapse makes the next real `call_dog()` slower/less reliable) but nothing had ever measured *how long* that timeout actually is, so the 2-minute interval was a guess with a large, wasted safety margin. The user identified the real number from the device's own reviews: the Sbode M400B auto-disconnects/powers down after **10 minutes** idle. **A naive fix (skip pinging overnight to save battery) was proposed and rejected**: the user pointed out the M400B doesn't just idle-disconnect from BlueZ (recoverable via a plain reconnect) — it powers itself off, which nothing on bosgame's side can remotely undo; skipping the ping would make the speaker fully unreachable until someone manually powers it back on, not just slower to reach. **Fixed instead** by widening `yard-speaker-ping.timer`'s `OnUnitActiveSec` from `2min` to `8min` (2 min of margin under the real 10-min cutoff, covering timer jitter and the ping's own connect+play latency) — cuts amp wake-ups roughly 4x, 24/7, with no loss of availability. Applies at all hours, not just overnight, since there was no reason the daytime interval needed to be that tight either.
- **Battery tracking added to confirm the 8-min interval actually helps** (2026-09-11): `yard_speaker.py` gained a `battery` action (`log_battery()`) that reads `bluetoothctl info`'s AVRCP `Battery Percentage` and `Connected` state and appends one CSV row (`timestamp,connected,battery_pct`) to `battery_log.csv` in the same directory — deliberately read-only, doesn't call `ensure_connected()`, so it never itself forces a reconnect or masks a real power-off with a fresh connect attempt. New `yard-speaker-battery-log.service`/`.timer` on bosgame samples it every 30 min (`Environment=XDG_RUNTIME_DIR=/run/user/1000`, same fix as the ping service needed). Baseline at setup: 70%. Check `battery_log.csv` after a few days to see whether the drain rate actually improved, and specifically whether overnight (22:00-08:00) rows show a flatter slope than before the interval change — that's the real test of whether 8 min was the right number, not just "under 10 min in theory."
- Pinned by `tests/test_wyze_subjects.py` (config, gate, verdict parsing) and the subject-sweep block in `tests/test_handle_transcript.py`.


---

### GPS / geolocation

Tiered pipeline: WiFi AP triangulation (Google Geolocation API → beacondb) → IP fallback (`ip-api.com`). `gps_locate()` cached 30 min. `_reverse_geocode` via Nominatim/OSM. `/gps` terminal command; `GET /gps` web endpoint.

**Three things had to be true before triangulation worked at all** (all found live 2026-07-30; before them every fix silently fell through to IP — 25 km, naming Ashcroft/Millbrook for a device in Fairview):

- **`GOOGLE_GEOLOC_KEY` must be in the *Pi's* `.env`.** It was only in the dev checkout, so the Google branch never ran. beacondb is not a substitute — it answered `"fallback":"ipf"` for these APs, i.e. no coverage, which `_wifi_geolocate` correctly rejects.
- **`nmcli dev wifi list` reports NetworkManager's *cached* scan.** Nothing asked for a fresh one, and on an idle Pi the cache held **one** AP against a two-AP minimum. An explicit `nmcli dev wifi rescan` takes it to 7. Rescan is privileged and the service runs as `ragnar`, so it needs `/etc/polkit-1/rules.d/50-zeev-wifi-scan.rules` (on the Pi, outside the repo; scoped to `org.freedesktop.NetworkManager.wifi.scan` only). Without the grant the cached list is still used, so it degrades rather than breaking.
- **`signalStrength` is dBm, not nmcli's percent.** Passing the percentage through is silently accepted and just makes the fix worse: same 7 APs measured **869 m with percent, 11 m with dBm** (`_nm_percent_to_dbm`, NM's own `2*(dBm+100)` inverted).

Result end-to-end: `[gps] fix: Fairview, Massachusetts, United States (±11m via wifi+google)`.

**Ambient awareness**: `_build_system_prompt` always appends `## Right now: <local time>` and `## Approximate location: <City, Region, Country>`. Before this the model saw location only when `needs_gps` matched — so it was blind to it on every other turn, and weather answers were location-aware only by accident, via a memorised fact naming the town (which goes stale on travel).

- The location block is **coarse on purpose** — no lat/lon, no accuracy — because it goes to Groq and OpenRouter on *every* turn; `gps_summary()` with coordinates stays behind `needs_gps`. `ZEEV_AMBIENT_LOCATION=0` disables it.
- Above `_AMBIENT_CITY_MAX_ACC` (5 km) it reports **region only**: an IP fix names the wrong town and the model repeats a wrong city as fact.
- The read path (`gps_cached()`) is **cache-only and never scans** — a cold `gps_locate()` is ~1.7s with the rescan, which must not sit in a turn. `_gps_refresh_loop` warms it every 20 min, deliberately **under** the 30-min TTL, or a cache-only reader keeps finding it expired. It also reverse-geocodes in the background, since a WiFi fix carries no place name and the block is nothing but place names.
- **The wall clock was missing from every prompt except the tool prompt.** With no clock the model confabulates instead of declining: observed 2026-07-29 21:02–21:04 answering "8:45 AM", then "2:45 PM" (inventing a calendar reading to justify it) while the Pi read 21:03 EDT. `_now_str()` uses `.astimezone()` — `datetime.now()` is naive, so `%Z` formats as empty and the zone vanishes while the string still looks fine.

**Street/POI/home-level vicinity** (`vicinity_place()`, added 2026-08-02): "on Lincoln Street" / "near Main Plaza" / "at home", built into `gps_summary()`, which stays behind `needs_gps` — never in the every-turn ambient block, which is coarse on purpose.

- `_reverse_geocode` moved from zoom=14 to **zoom=18** (building level) to get `address.road` and a POI `name` ("Main Plaza") at all — zoom=14 never returned them, only suburb/city. Verified live against Nominatim that `city` is identical at both zooms, so this doesn't touch the ambient block.
- Gated on `_VICINITY_MAX_ACC` (50 m), separately from `_AMBIENT_CITY_MAX_ACC` — Nominatim returns the nearest road for *any* coordinate regardless of fix quality, so an IP fix (~25 km) or a poor beacondb fix would otherwise name a street with no basis. A missing `accuracy` also fails toward silence, not toward asserting — the opposite default of most fields in this module.
- **Home is opt-in via `ZEEV_HOME_LAT`/`ZEEV_HOME_LON`/`ZEEV_HOME_RADIUS`** (metres, default 100) in the Pi's `.env` — not in git, same as `GOOGLE_GEOLOC_KEY`, so without it "am I at home" degrades to a street name forever, never to a wrong "no". Parsed via `_env_float()`, which skip-and-logs a malformed value rather than crashing the whole app at import — same constraint as `parse_subjects()` for `ZEEV_SUBJECTS`. When home is configured and the fix is within radius it wins outright over naming the street, since that's the answer that's actually useful for a fix sitting in the driveway.
- The three call sites (prompt block, `/gps`, the refresh loop) used to each gate their own `_reverse_geocode` call on `not loc.get("city")` — meaning once the refresh loop filled in `city`, later sites never called it again, so `road`/`poi` (added after `city` already worked) could never populate on an already-warm cache entry. Unified into `_geocoded_location()`, gated on a `_geocoded` sentinel (tried-once, not "succeeded"), enriched dict written back to `_gps_cache` so it's shared.
- `_GPS_RE` widened for `(what|which) (street|road) am i on`, `am i at home` — the coarser `where am i` phrasing already matched, but street/home-specific asks didn't, which would have been the enrichment landing with no gate to reach it (the angelic-prayer failure shape).
- The road claim is worded **"approximately on X Street"** — the one claim here without a firm basis like a home radius or a named POI, and the model restates prompt-block text as settled fact, so the hedge has to live in the words themselves.
- Pinned by `tests/test_gps_vicinity.py`.

**Two live-found gaps, both fixed 2026-09-03** (`/test-and-fix`-style investigation of a "why did it say Ashcroft, we're in Hartwell" report):

- **A stored "Alex lives in X" fact was used to answer a live "where are you right now" question.** Asked "do you know where you are?" with no `needs_gps` match on that exact phrasing, the model pulled a `facts`-table entry ("Alex lives in Fairview, Massachusetts") and presented it as current location — correct for a home address, not necessarily for where the device actually is (e.g. while traveling). Fixed: the `## What I know about Alex:` block now carries an explicit instruction that a "lives in"/"is from" fact is a home address, not current location, and to say "don't know" rather than answer from it when no location block is present.
- **A coarse IP-based fix was spoken as precise fact.** A post-reboot cold GPS cache fell through to IP geolocation (~25km accuracy) and resolved to a point in a neighboring town, Ashcroft — a known real-world IP-geolocation quirk — for a device actually in Hartwell, well inside that margin. `gps_summary()`'s formatted string always includes 5-decimal coordinates and a city name regardless of fix quality (by design, for legitimate precise fixes), and nothing told the model this particular fix was untrustworthy — it repeated "Ashcroft, Massachusetts" with specific-looking coordinates and full confidence, dropping the accuracy/method entirely. Fixed: the `## Current location:` block now appends an explicit hedge instruction whenever `method == "ip"` or `accuracy >= 10000` — do not state the city/coordinates as precise fact, say only that it's a rough regional guess. Pinned by three new tests in `tests/test_ambient_context.py`.


---

## bosgame Kokoro TTS server

Primary English TTS: **Kokoro** on bosgame. Pi daemon calls `https://ollama.sogdiana-gematria.net/piper/tts`.

- **Server**: `~/piper/tts_server.py` (port 5600, localhost-only), service `piper-tts.service`. `PIPER_MODELS` maps `"lang"` → Piper model (`ru`→`ru_RU-irina-medium.onnx`, `es`→`es_AR-daniela-high.onnx`); any other/no lang uses Kokoro, with English Piper as its own error fallback.
- **Kokoro**: `~/kokoro/kokoro-v1.0.onnx` + `voices-v1.0.bin`. Default voice: `af_heart` (Sarina, 24kHz, speed=1.0). Set via `KOKORO_VOICE` env var or per-request `"voice"` field. **RTF ~0.82 on bosgame's CPU** (AMD Ryzen 5 3550H, no GPU), close to real-time; int8/fp16 quantization confirmed NOT to help this CPU (no AVX512-VNNI/native fp16 — int8 measured 3x *slower*) — fp32 is already fastest here.
- **Piper fallback**: `~/piper/en_US-lessac-medium.onnx` (22050Hz), ~0.7s latency.
- **Go daemon** (`REMOTE_PIPER_URL`): parses WAV header bytes 24-27 for sample rate. `REMOTE_PIPER_VOICE` sets default voice; falls back to `BOSGAME_KEY` if `REMOTE_PIPER_KEY` unset. Per-request `"voice"` overrides. Shared `RemotePiperClient` (10min idle timeout) + background warmup in `Init()` keep the connection warm across multi-minute gaps between turns.
- **Second backend (feiergente01)**: `REMOTE_PIPER_URL2`/`REMOTE_PIPER_KEY2` → second Kokoro instance on `feiergente01` (Windows 11, i7-1360P with **Iris Xe integrated GPU** — corrected 2026-08-06, an earlier "no GPU" note here was wrong, LAN `10.0.0.208:5601`, RTF ~0.68-0.71 measured direct-LAN). `speakPiper` alternates sentence chunks between backends (bosgame even, feiergente01 odd) — separate machines avoid same-backend contention (single-threaded `tts_server.py`). Cuts a 99-word/5-sentence reply's overhead from ~10s+ to ~3-4s.
  - **Ollama also runs on feiergente01** (`10.0.0.208:11434`), fully offloaded onto the same Iris Xe iGPU (`qwen2.5:7b-instruct-q4_K_M` installed 2026-08-06, `size_vram == size`). **This iGPU is shared, not parallel** — measured live: Kokoro TTS baseline ~3.6s for a ~5s clip degraded to 4.9s/7.8s/8.1s (up to 2.2x) across three sequential requests fired while qwen2.5 was mid-generation, and the concurrent qwen request itself exceeded 60s (vs. ~13-16s standalone for a similar-length reply). Latency returned to baseline (~2.3s) immediately once the qwen load cleared — contention, not a crash. Because Kokoro on this box is in the live `speakPiper` path, **any Ollama workload here can add multiple seconds to a real user's in-flight reply** if it happens to overlap.
  - **Guard built 2026-08-06** (`extract_memory`/`weekly_reflection.py` testing only — nothing in a live turn calls feiergente01's Ollama). `remotePiperSynthAt` in `zeev-audio/internal/server/handlers.go` touches `/tmp/zeev-feiergente-busy.lock` for the duration of any request whose URL is `RemotePiperURL2`, removed via `defer` on return (`markFeiergenteBusy`). Python's `_feiergente_busy()` (in `zeev.py` and `weekly_reflection.py`, duplicated rather than shared since they're separate entry points) checks that file's mtime and treats anything under 30s old as busy, older as a stale lock from a crashed daemon. `FEIERGENTE_URL`/`FEIERGENTE_MODEL` env vars gate the whole path — empty by default, so this is opt-in even with the code merged. `_feiergente_complete()`/`_call_feiergente()` sit ahead of the existing bosgame/Groq chain in both files and fail silently through to it on any error (including "busy"), so enabling this can only add a faster/better-quality attempt, never remove the existing fallback behavior. Verified live: correct JSON fact extraction from feiergente01's qwen2.5, lock correctly blocks while fresh and is ignored once stale (tested via mtime manipulation, not a live TTS race). **Not yet tested**: an actual concurrent live-TTS-vs-Ollama race with the guard active — only the file-mtime logic itself and the plain no-contention completion path have been verified so far.
  - **Public exposure**: `REMOTE_PIPER_URL2=https://ollama.sogdiana-gematria.net/piper2/tts`, key = its own `REMOTE_PIPER_KEY2` (set as an `Environment=` line on the `zeev-audio` systemd unit, not in `.env`) — **not** `BOSGAME_KEY`; a request to `/piper2/tts` using `BOSGAME_KEY` 403s (verified live 2026-09-03). Location is in `/etc/nginx/sites-available/ollama.sogdiana-gematria.net`, **not** `sites-available/default` (that vhost excludes the `ollama.` subdomain and returns 444 — cost real debugging time once).
  - **One dead backend used to garble every multi-sentence reply** (found live 2026-07-29, feiergente01 powered off). A failed chunk aborted `speakPiper`, and the handler's espeak fallback then re-spoke the **entire** text — heard as Kokoro delivering sentence one and espeak restarting the reply from the top, on 2 of 4 turns. Only long replies were affected: a single-chunk reply never reaches the odd index that goes to the second backend, so the failure looked intermittent and voice-related when it was purely a length threshold. `synthOne` now retries the chunk on the primary and benches the failed backend for `backend2Cooldown` (2 min) — a 502 costs ~2s, so probing a dead backend once per odd chunk taxes the whole outage. `writePCM` takes each chunk's **own** rate rather than chunk 0's, because a failover can return 22050Hz Piper where 24kHz Kokoro was expected and `aplay` is opened once for the reply.
  - **Voice names are mapped Python-side, before the daemon** (`zeev.py:8526`): `daniel`→`am_adam`, `sarina`→`af_heart`. Sending a persona name straight through is not harmless and the two backends disagree about it (**found by probing the endpoints directly, not from a device turn** — the mapping means production never sends the raw name, so this is a latent trap, not an active bug): bosgame's `tts_server.py` catches the Kokoro error and **silently degrades to English Piper (lessac), returning 200**, while feiergente01 returns 500. So an unmapped voice yields the wrong voice on one machine and a hard failure on the other, with no obvious error on either. `grep 'kokoro failed' ` in bosgame's `piper-tts` journal is the tell.
  - **feiergente01 setup**: `C:\kokoro\tts_server.py`, Windows service `ZeevTTS` via NSSM (auto-restart), logs `C:\kokoro\service_*.log`, firewall TCP 5601 inbound.
    - **Not tied to any login** — `sc qc ZeevTTS` confirms `SERVICE_START_NAME: LocalSystem` and `START_TYPE: AUTO_START`, so it runs at the lock screen and survives logoff, a different user logging in, and fast user switching. What *does* take it down is the machine sleeping, so check power settings before suspecting the service. `DEPENDENCIES` is empty, so on a cold boot it starts before the network is necessarily up — brief failures right after a feiergente reboot are expected and covered by NSSM restart plus the Pi's 2-minute backend cooldown, not a fault. **Gotcha**: stale per-user `typing_extensions` can shadow the global copy for the SYSTEM service — fix with `pip install --target=C:\Python314\Lib\site-packages --upgrade <pkg>`.
- **Voice personas**: Zeev's brain voice = Groq Orpheus `daniel`; device mode speaker = Kokoro `af_heart` ("Sarina", Zeev's partner).


---

### Device-mode turn handling

`handle_transcript(ctx, transcript)` and `finish_turn(ctx, ...)` are **module-level**, not closures. They were 654 lines buried inside `run_device_mode` (which needs the HAT to import), so the intent router — ~19 branches, the largest piece of device-mode logic — could not be imported or tested at all. `run_device_mode` went 2225 → 1597 lines.

- They stay in `zeev.py` rather than a separate module on purpose: they call ~50 module-level functions here (`route_model`, `needs_torah`, `_build_system_prompt`, `extract_bt_intent`, `run_tool_calls`, `youtube_play` …), so a new module means a circular import or prefixing every call — a large diff whose only benefit is file location.
- **`ctx.session` is the single source of truth.** The handler rebinds it (`ctx.session = ctx.session[-60:]`) while `finish_turn` appends. Two separate references would leave `finish_turn` appending to the pre-truncation list and history would silently stop growing.
- `_DeviceCtx` uses `__slots__`. Tests subclass it (a subclass without its own `__slots__` gains a `__dict__`) rather than widening the production class.
- **Voice is resolved once, at the top of the turn**, from `_WAKE_VOICE` if a wake word set it, else inferred from the transcript regex. It used to be resolved only in the LLM fallthrough, so a wake word picked the voice for chat but *not* for music/jokes/language-switch, and the unconsumed value leaked into the next turn. `finish_turn` defaults to `_LAST_VOICE` so every branch honours it. Caught by `tests/test_handle_transcript.py`, not by hand.
- **Goodnight is the one branch that answers in two voices** — Zeev (`daniel`) then Sarina (`sarina`), deliberately ignoring `_WAKE_VOICE`, since both answering is the whole point. Every other reply is spoken by `finish_turn` in a single voice, which is why it gained **`speak=False`**: the branch has already said its piece, but both lines still belong in the history. Placed high (just below the language switch, which outranks it) so no later gate can swallow a sign-off, gated to the **first 40 chars**, and excluding `_TOOL_INTENT_RE` so "remind me to say goodnight at nine" stays a reminder. A negative lookahead rejects "a good night **light**" and "a good night**'s** sleep". **Every pair in `_GOODNIGHT_LINES` names everyone in `_GOODNIGHT_HOUSEHOLD`** (Alex, Maria, Leo, Smokey) and both voices address Alex — a random choice that sometimes dropped someone would make the wish intermittent, which reads worse than not having it. Pinned by the goodnight block in `tests/test_handle_transcript.py`.

- **`detect_active_speaker()` only ever trusts a USER's own words — never Zeev's.** Found live 2026-08-07: both Zeev and Sarina answered Alex with "...thanks for asking, Maria." A since-removed branch also scanned ASSISTANT replies for a bare `"maria, "`/`" maria."` substring, on the theory that if Zeev just addressed Maria, she must be the one talking — a category error that self-reinforces. The goodnight branch above deliberately names the whole household in every reply, so one goodnight turn planted "Maria" in session history; the next turn's scan found it, misattributed the speaker as Maria, and Zeev addressed Alex as Maria — planting *another* "Maria" mention for the next scan to find. Once triggered, the loop never self-corrects, since nothing in it is the user's own words; the real message history for this incident had no genuine Maria self-identification anywhere, only Alex talking about his wife. Fixed by removing the assistant-scanning branch entirely and bounding the user-message scan to the last 6 turns, so a genuine "this is Maria" self-identification doesn't keep reassigning the speaker indefinitely after Alex has plainly resumed talking. Pinned by two tests in `tests/test_tts_pipeline.py`, one using the real incident's exact trigger text.
- **"Truncated" is inferred, and a false positive is expensive.** `finish_turn` treats a reply not ending at a terminator as evidence the model ran out of tokens: it appends **"Want to hear more?"**, speaks it, **rewrites the stored history** to the modified text, arms the follow-up listener and pre-generates a continuation. So `_last_complete_sentence()` allows closing punctuation **after** the terminator (`_SENTENCE_TAIL_RE`) — a reply ending `…to you!"` left the quote over under the old `^(.*[.!?])` and all four consequences fired on a complete reply (live 2026-08-01 19:07, logged `113/114 chars`; the missing character *was* the quote). No terminator at all still means speak it whole.
- **A follow-up "yes" must be a *bare* yes** (`_plain_affirmative()`): no pivot (`_MORE_PIVOT_RE` — but/however/actually/…) and no `?`. *"Yes, but can you sing in harmony together with Zeev?"* matched on its first word, so the canned pre-generated detail was delivered and the real question was never answered or even seen. The mirror case is fixed with it: a non-affirmative follow-up used to be **discarded outright**, so "No, what's the weather?" meant Zeev asked, Alex answered, and nothing happened — one carrying a question is now handled as its own turn, while a bare decline stays silent. Pinned by `tests/test_sentence_truncation.py`.
- **Real `finish_reason` is now logged, not just inferred** (2026-08-06, `llm_finish_log` table). The Groq/OpenAI-compatible API reports `"stop"` vs `"length"` on every completion, but `zeev.py` discarded it entirely until now — `_last_complete_sentence()` above is the only truncation signal that ever existed, and it guesses from spoken text rather than reading the API's own answer. `_iter_llm_tokens(resp, provider, on_finish=...)` now surfaces it for the `groq`/`openai`/`openrouter` SSE branch; `_log_llm_finish(path, model, max_tokens, finish_reason, reply_chars)` writes one row per completion. Wired into the three highest-traffic chat paths only (`device_chat` via `finish_turn`'s call to `ctx._stream_speak`, `web_chat` in `run_web_server`'s inline SSE loop, `terminal_chat` in `stream_reply`) — not tool calls, Torah, thermal, or any background path. Purely additive: no generation behavior changes, and a DB write failure is caught and logged, never raised into a live turn. No `--report` script yet; query `llm_finish_log` directly (e.g. `SELECT path, model, finish_reason, COUNT(*) FROM llm_finish_log GROUP BY 1,2,3`) until there's enough real traffic to make a dashboard worthwhile.
- **The pending-detail topic used to compound a "Give me more detail on that." suffix on every consecutive pre-generation failure.** Live 2026-08-06 (same night the OpenRouter fallback candidate turned out to be unreliable — see `_groq_post_with_fallback` above): asking Zeev about Ezekiel, then saying "yes" to "Want to hear more?" twice in a row while the background pre-generation kept failing, produced `"Tell me about Ezekiel. Give me more detail on that. Give me more detail on that."` as the re-asked prompt, and the second "more" reply came back a near-verbatim repeat of the first. Root cause: the fallback branch (`## Pending detail expansion`) rewrites `transcript` into `"<topic> Give me more detail on that."` for that turn's own routing/LLM call, and the rewritten value was then being stored as `_pending_detail_source[0]` for the *next* round too — so each consecutive failure appended another copy of the suffix onto an already-suffixed topic. **Fixed**: a separate `topic_for_pending` variable, initialized to the clean `transcript` at the top of `handle_transcript` and only ever reassigned to the already-clean `source` (never to the rewritten `transcript`) inside the fallback branch — `_pending_detail_source[0]` now always stores a clean topic regardless of how many rounds fail in a row. Pinned by `test_pending_detail_topic_does_not_compound_on_repeated_failure` in `tests/test_handle_transcript.py` (verified to actually fail against the pre-fix code before confirming the fix). **Not fixed by this**: if pre-generation keeps failing on every single round, the fallback still re-sends the same clean prompt each time, so near-duplicate replies are still possible — this fix stops the prompt from getting progressively longer and more garbled, it doesn't guarantee content freshness under sustained fallback failure.
- **`_FOLLOWUP_MAX_DEPTH` was 2, one round short of a real "read me the passage" flow.** Live 2026-08-09 asking Zeev to read the week's Torah portion: two "yes, more" rounds (depth 0→1→2) each ended in another "Want to hear more?", and by the third detail reply — which asked its own trailing question ("Would you like me to start reading from Deuteronomy 11:26?") — `_followup_turn` (`zeev.py:10945`) hit `depth >= _FOLLOWUP_MAX_DEPTH` and returned before ever calling `_followup_listen()`, so the mic silently stayed shut and a wake word was needed to continue. Raised to 3. Same "user cannot walk away" ceiling still applies, just one round later. Same night: `_prefetch_detail` (`zeev.py:12045`) hit a raw `KeyError: 'choices'` when a Groq 429 fell through to an OpenRouter response missing the expected shape — `r.json()["choices"][0]...` now reads via `.get("choices")`/`.get("message", {})` so a malformed fallback body degrades to "pre-generation failed or empty" instead of crashing the background thread.

---

### Device-mode turn handling

`handle_transcript(ctx, transcript)` and `finish_turn(ctx, ...)` are **module-level**, not closures. They were 654 lines buried inside `run_device_mode` (which needs the HAT to import), so the intent router — ~19 branches, the largest piece of device-mode logic — could not be imported or tested at all. `run_device_mode` went 2225 → 1597 lines.

- They stay in `zeev.py` rather than a separate module on purpose: they call ~50 module-level functions here (`route_model`, `needs_torah`, `_build_system_prompt`, `extract_bt_intent`, `run_tool_calls`, `youtube_play` …), so a new module means a circular import or prefixing every call — a large diff whose only benefit is file location.
- **`ctx.session` is the single source of truth.** The handler rebinds it (`ctx.session = ctx.session[-60:]`) while `finish_turn` appends. Two separate references would leave `finish_turn` appending to the pre-truncation list and history would silently stop growing.
- `_DeviceCtx` uses `__slots__`. Tests subclass it (a subclass without its own `__slots__` gains a `__dict__`) rather than widening the production class.
- **Voice is resolved once, at the top of the turn**, from `_WAKE_VOICE` if a wake word set it, else inferred from the transcript regex. It used to be resolved only in the LLM fallthrough, so a wake word picked the voice for chat but *not* for music/jokes/language-switch, and the unconsumed value leaked into the next turn. `finish_turn` defaults to `_LAST_VOICE` so every branch honours it. Caught by `tests/test_handle_transcript.py`, not by hand.
- **Goodnight is the one branch that answers in two voices** — Zeev (`daniel`) then Sarina (`sarina`), deliberately ignoring `_WAKE_VOICE`, since both answering is the whole point. Every other reply is spoken by `finish_turn` in a single voice, which is why it gained **`speak=False`**: the branch has already said its piece, but both lines still belong in the history. Placed high (just below the language switch, which outranks it) so no later gate can swallow a sign-off, gated to the **first 40 chars**, and excluding `_TOOL_INTENT_RE` so "remind me to say goodnight at nine" stays a reminder. A negative lookahead rejects "a good night **light**" and "a good night**'s** sleep". **Every pair in `_GOODNIGHT_LINES` names everyone in `_GOODNIGHT_HOUSEHOLD`** (Alex, Maria, Leo, Smokey) and both voices address Alex — a random choice that sometimes dropped someone would make the wish intermittent, which reads worse than not having it. Pinned by the goodnight block in `tests/test_handle_transcript.py`.

- **`detect_active_speaker()` only ever trusts a USER's own words — never Zeev's.** Found live 2026-08-07: both Zeev and Sarina answered Alex with "...thanks for asking, Maria." A since-removed branch also scanned ASSISTANT replies for a bare `"maria, "`/`" maria."` substring, on the theory that if Zeev just addressed Maria, she must be the one talking — a category error that self-reinforces. The goodnight branch above deliberately names the whole household in every reply, so one goodnight turn planted "Maria" in session history; the next turn's scan found it, misattributed the speaker as Maria, and Zeev addressed Alex as Maria — planting *another* "Maria" mention for the next scan to find. Once triggered, the loop never self-corrects, since nothing in it is the user's own words; the real message history for this incident had no genuine Maria self-identification anywhere, only Alex talking about his wife. Fixed by removing the assistant-scanning branch entirely and bounding the user-message scan to the last 6 turns, so a genuine "this is Maria" self-identification doesn't keep reassigning the speaker indefinitely after Alex has plainly resumed talking. Pinned by two tests in `tests/test_tts_pipeline.py`, one using the real incident's exact trigger text.
- **"Truncated" is inferred, and a false positive is expensive.** `finish_turn` treats a reply not ending at a terminator as evidence the model ran out of tokens: it appends **"Want to hear more?"**, speaks it, **rewrites the stored history** to the modified text, arms the follow-up listener and pre-generates a continuation. So `_last_complete_sentence()` allows closing punctuation **after** the terminator (`_SENTENCE_TAIL_RE`) — a reply ending `…to you!"` left the quote over under the old `^(.*[.!?])` and all four consequences fired on a complete reply (live 2026-08-01 19:07, logged `113/114 chars`; the missing character *was* the quote). No terminator at all still means speak it whole.
- **A follow-up "yes" must be a *bare* yes** (`_plain_affirmative()`): no pivot (`_MORE_PIVOT_RE` — but/however/actually/…) and no `?`. *"Yes, but can you sing in harmony together with Zeev?"* matched on its first word, so the canned pre-generated detail was delivered and the real question was never answered or even seen. The mirror case is fixed with it: a non-affirmative follow-up used to be **discarded outright**, so "No, what's the weather?" meant Zeev asked, Alex answered, and nothing happened — one carrying a question is now handled as its own turn, while a bare decline stays silent. Pinned by `tests/test_sentence_truncation.py`.
- **Real `finish_reason` is now logged, not just inferred** (2026-08-06, `llm_finish_log` table). The Groq/OpenAI-compatible API reports `"stop"` vs `"length"` on every completion, but `zeev.py` discarded it entirely until now — `_last_complete_sentence()` above is the only truncation signal that ever existed, and it guesses from spoken text rather than reading the API's own answer. `_iter_llm_tokens(resp, provider, on_finish=...)` now surfaces it for the `groq`/`openai`/`openrouter` SSE branch; `_log_llm_finish(path, model, max_tokens, finish_reason, reply_chars)` writes one row per completion. Wired into the three highest-traffic chat paths only (`device_chat` via `finish_turn`'s call to `ctx._stream_speak`, `web_chat` in `run_web_server`'s inline SSE loop, `terminal_chat` in `stream_reply`) — not tool calls, Torah, thermal, or any background path. Purely additive: no generation behavior changes, and a DB write failure is caught and logged, never raised into a live turn. No `--report` script yet; query `llm_finish_log` directly (e.g. `SELECT path, model, finish_reason, COUNT(*) FROM llm_finish_log GROUP BY 1,2,3`) until there's enough real traffic to make a dashboard worthwhile.
- **The pending-detail topic used to compound a "Give me more detail on that." suffix on every consecutive pre-generation failure.** Live 2026-08-06 (same night the OpenRouter fallback candidate turned out to be unreliable — see `_groq_post_with_fallback` above): asking Zeev about Ezekiel, then saying "yes" to "Want to hear more?" twice in a row while the background pre-generation kept failing, produced `"Tell me about Ezekiel. Give me more detail on that. Give me more detail on that."` as the re-asked prompt, and the second "more" reply came back a near-verbatim repeat of the first. Root cause: the fallback branch (`## Pending detail expansion`) rewrites `transcript` into `"<topic> Give me more detail on that."` for that turn's own routing/LLM call, and the rewritten value was then being stored as `_pending_detail_source[0]` for the *next* round too — so each consecutive failure appended another copy of the suffix onto an already-suffixed topic. **Fixed**: a separate `topic_for_pending` variable, initialized to the clean `transcript` at the top of `handle_transcript` and only ever reassigned to the already-clean `source` (never to the rewritten `transcript`) inside the fallback branch — `_pending_detail_source[0]` now always stores a clean topic regardless of how many rounds fail in a row. Pinned by `test_pending_detail_topic_does_not_compound_on_repeated_failure` in `tests/test_handle_transcript.py` (verified to actually fail against the pre-fix code before confirming the fix). **Not fixed by this**: if pre-generation keeps failing on every single round, the fallback still re-sends the same clean prompt each time, so near-duplicate replies are still possible — this fix stops the prompt from getting progressively longer and more garbled, it doesn't guarantee content freshness under sustained fallback failure.
- **`_FOLLOWUP_MAX_DEPTH` was 2, one round short of a real "read me the passage" flow.** Live 2026-08-09 asking Zeev to read the week's Torah portion: two "yes, more" rounds (depth 0→1→2) each ended in another "Want to hear more?", and by the third detail reply — which asked its own trailing question ("Would you like me to start reading from Deuteronomy 11:26?") — `_followup_turn` (`zeev.py:10945`) hit `depth >= _FOLLOWUP_MAX_DEPTH` and returned before ever calling `_followup_listen()`, so the mic silently stayed shut and a wake word was needed to continue. Raised to 3. Same "user cannot walk away" ceiling still applies, just one round later. Same night: `_prefetch_detail` (`zeev.py:12045`) hit a raw `KeyError: 'choices'` when a Groq 429 fell through to an OpenRouter response missing the expected shape — `r.json()["choices"][0]...` now reads via `.get("choices")`/`.get("message", {})` so a malformed fallback body degrades to "pre-generation failed or empty" instead of crashing the background thread.
