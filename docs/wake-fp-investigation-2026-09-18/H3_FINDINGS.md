# H3 findings: does Zeev wake itself on its own speech?

**VERDICT: REFUTED** (as a live self-triggering mechanism; see caveats below).

## Summary

Two independent lines of evidence were tested. Neither supports the "Zeev's
own TTS output (especially replies containing 'Zeev'/'Sarina') triggers the
wake models" hypothesis. Additionally, reading the actual wake-loop code
(`zeev/zeev.py`, `_wake_loop_oww`) shows the architecture already structurally
prevents most of this failure mode: the mic is released entirely whenever
`_face_state` is not `idle`/`ready` (i.e. during "speaking"), and a 1.5s
`OWW_SETTLE` delay plus `model.reset()` runs before the mic re-arms after a
turn ends, specifically to avoid the tail of Zeev's own reply retriggering
the model. This isn't a proposed fix — it's already in production.

## 1. Offline: does Zeev's own voice score high?

**Harness**: `h3work/score_dist.py`, run under the shared wake-lab venv
(`/home/azaurov/homelab-ops/wake-lab/venv/bin/python`), scoring 1280-sample
(80ms) frames with `openwakeword.model.Model` loaded with both
`hey_zeev.onnx` and `hey_sarina.onnx`. Validated first against the control
corpus alone before any TTS audio was scored.

**Control corpus** (per coordinator instruction, re-globbed at run time,
ALL `neg_*.wav`, not just the original thin sample):
- `neg_2.wav` (120.1s) + `neg_raw_00001.wav` (600.0s) = **720.1s (12.0 min)**
  of continuous news/talk speech, 16kHz mono.
- Pooled stats (9000 frames each model):

  | model | max | p99 | p95 | mean | frames ≥0.5/0.68/0.84/0.95 |
  |---|---|---|---|---|---|
  | hey_zeev | 0.0426 | 0.00046 | 0.00009 | 0.0000547 | 0 / 0 / 0 / 0 |
  | hey_sarina | 0.0135 | 0.00046 | 0.00014 | 0.0000446 | 0 / 0 / 0 / 0 |

  Confirmed stable across 3 identical runs (deterministic inference, no
  randomness in the ONNX models — see `run 1/2/3` output, identical to the
  printed precision each time).

**Zeev's own TTS** (14 phrases, synthesized via the real production
endpoint `POST https://ollama.sogdiana-gematria.net/piper/tts`, voices
`am_adam` (Zeev) / `af_heart` (Sarina), resampled 24kHz→16kHz mono with
ffmpeg — the same pipeline path Kokoro output takes before Piper/PCM
playback): phrases explicitly containing "Zeev" and/or "Sarina" spoken in
character (self-identification, referring to the other persona by name),
phonetically-adjacent near-misses ("Hey, did you see...", "Have you
eaten?", "Hey, Serena called..."), and plain replies with neither name.

  | phrase (abbrev) | hey_zeev max | hey_sarina max |
  |---|---|---|
  | "Hi Alex, this is Zeev..." | 0.000146 | 0.0000844 |
  | "Zeev here, I found the weather..." | 0.0000730 | 0.000245 |
  | "I am Zeev, your voice assistant." | 0.000271 | 0.0000849 |
  | "Sarina and I were just talking..." | 0.0000434 | 0.000249 |
  | "Let me ask Sarina what she thinks." | 0.000152 | 0.000259 |
  | "Hi Alex, it's Sarina. Zeev is..." | 0.0000648 | 0.000743 |
  | "Zeev and I are here for you..." | 0.0000662 | 0.000252 |
  | "The current temperature is..." (plain) | 0.0000312 | 0.0000299 |
  | "I've set a reminder for..." (plain) | 0.0000214 | 0.000672 |
  | "Sure thing, I can play music..." (plain) | 0.0000415 | 0.000187 |
  | "Hey, did you see that?" (near-miss) | 0.000583 | 0.000142 |
  | "Have you eaten?" (near-miss) | 0.0000917 | 0.0000286 |
  | "Hey, Serena called earlier..." (near-miss) | 0.000218 | **0.00664** |
  | "A heavy rain is coming..." (near-miss) | 0.0000390 | 0.00344 |

**Every single TTS phrase — including the ones that say "Zeev" or "Sarina"
outright, spoken by that persona's own voice — scored BELOW the control
corpus's own peak** (0.0426 hey_zeev / 0.0135 hey_sarina). The single
highest TTS score across all 14 phrases and both models is 0.00664
(hey_sarina, on "Hey, Serena called earlier about the trip" — a
phonetic near-miss, not a self-identification phrase), still ~2x below the
control's own peak and ~127x below the production `hey_sarina` threshold
(0.95, per project memory `wake_word_threshold_tuning.md`) and ~12x below
the lowest historically-considered threshold (0.66/0.68 for hey_zeev).

**This directly contradicts the premise that saying "Zeev"/"Sarina" in
character is a meaningfully elevated risk relative to ordinary speech** —
if anything, in this sample, ordinary continuous talk-radio speech scored
higher than clean synthesized self-referential TTS.

## 2. Live: do real false wakes cluster around Zeev's own speech?

**Method**: `journalctl -u zeev-device` mined for `[wake] ... trigger
(score ...)` lines over the last 14 days (all real trigger events in that
window — 14 total), then ±20s/+5s context pulled around each exact
timestamp to check for adjacent `Done`/TTS/reply lines.

**14 real triggers, Sep 13–17 2026** (`h3work/triggers_14d.txt`,
`h3work/trigger_context.txt` — full journal quotes with real timestamps):

- **7 of the 14** fall inside one coherent Rosh Hashanah conversation
  (2026-09-13 09:03:08–09:07:11), alternating with real transcribed user
  turns (e.g. `Sep 13 09:04:39 ... You: Wishes happy holidays for Rosh
  Hashanah.` sitting between two `hey_sarina` triggers) — this reads as a
  real multi-turn conversation with the user re-invoking the wake word
  each turn, not a self-triggering loop. The gaps between each
  `[+X.Xs] Done` (end of Zeev's own TTS) and the next trigger are 13–24s,
  far outside the 1.5s `OWW_SETTLE` re-arm window where a tail-of-speech
  self-trigger would have to land.
- **1 trigger** (2026-09-16 23:02:01) precedes `You: What's the spiel?`
  and the shpeel reply — an ordinary genuine wake+question.
- **5 triggers** (Sep 13 19:47:54, Sep 14 20:34:56, Sep 16 00:19:36,
  Sep 17 22:24:15, Sep 17 23:00:55) have **no Zeev speech anywhere in the
  surrounding journal window at all** — no `Done`, no TTS line, nothing.
  These cannot be attributed to Zeev's own voice by construction: there
  was no Zeev audio playing anywhere near them.

**No trigger in this 14-day window occurs inside the `OWW_SETTLE` (1.5s)
self-trigger window immediately after Zeev finishes speaking.** The
closest observed gap between a `Done` line and a subsequent trigger is 13s
— consistent with genuine renewed conversation, not tail-of-reply bleed.

## What this does NOT establish

- **Clean pre-enclosure TTS is not the same as post-enclosure audio.**
  These WAVs were synthesized and scored directly — they never traveled
  speaker → 3D-printed enclosure → mic. Reflection/resonance inside the
  closed enclosure (acknowledged in project docs as having zero echo
  cancellation) could plausibly boost or reshape the spectral content in
  ways that raise scores. This offline result rules out "the words
  themselves are inherently risky" but cannot rule out "the enclosure's
  acoustics happen to color Zeev's specific voice into something
  wake-shaped." That would require an on-device recording of the mic
  picking up real Zeev speaker output, which task rules explicitly forbid
  here (no device playback, household asleep).
- **No genuine-wake corpus exists tonight.** There's no positive/genuine
  "hey zeev"/"hey sarina" said-by-a-human corpus available in this
  session, so there's no way to show real-wake sensitivity would survive
  unchanged if any mitigation were applied — moot here since no fix is
  being proposed, but noted per the task's own honesty requirement.
- **Small control sample relative to a full night.** 12 minutes of
  talk-radio speech is a reasonable spot-check but is not the volume of
  ambient audio a Pi hears over hours; it establishes "TTS phrases don't
  stand out from ordinary speech at this sample size," not "TTS phrases
  can never exceed threshold under any circumstance."
- **14 trigger events over 14 days is a thin sample** for the live
  correlation. It happens to contain zero events landing in the
  self-trigger-relevant time window, but with only 14 data points that
  absence is suggestive, not proof of a zero rate — a longer journal
  history (rotated out, per the task's own caveat about journal rotation
  — this box's journal only went back reliably to Sep 13) would
  strengthen this further.
- **This does not investigate the other 5 (unattributable) triggers at
  all** — they are evidence *against* H3 specifically (no Zeev speech
  nearby), but they are unexplained and are presumably another sibling
  agent's hypothesis (TV/ambient audio, wake-word threshold miscalibration,
  etc.), not something this task should speculate on further.

## Files in this worktree

- `h3work/synth.py` — synthesizes the 14 TTS test phrases via bosgame's
  real Kokoro/Piper endpoint (reads `h3work/.key`, a git-ignored,
  chmod-600, never-printed copy of `BOSGAME_KEY`).
- `h3work/resample.sh` — 24kHz→16kHz mono conversion (ffmpeg) matching the
  model's required input format.
- `h3work/score.py` — quick single-shot peak scorer (used for initial
  validation).
- `h3work/score_dist.py` — the real harness: re-globs the control corpus,
  computes full frame-score distributions (max/p99/p95/mean/threshold
  counts) for both models, pooled and per-file, then does the same for
  every TTS phrase. Writes `h3work/score_dist_results.json`.
- `h3work/tts/`, `h3work/tts16/` — raw and resampled synthesized audio.
- `h3work/pi_journal.sh`, `pi_triggers.sh`, `pi_context.sh` — read-only,
  lock-wrapped SSH journal queries (no device changes made).
- `h3work/triggers_14d.txt`, `trigger_context.txt`,
  `score_dist_results.json` — raw evidence backing the tables above.

No code changes proposed. Since the offline and live evidence both point
away from Zeev's own speech as the false-wake mechanism, and the
architecture already contains the mitigation the task described as
speculative (mic released during `speaking`, `OWW_SETTLE` + `model.reset()`
on re-arm), there is nothing to gate further under this hypothesis. If a
future investigation wants to pursue enclosure acoustics specifically, the
next step would be a live on-device mic recording of Zeev's actual reply
audio (during a scheduled awake/testing window, not tonight) fed back
through this same harness.

## Addendum: the 5 unattributed triggers, closed out

Per follow-up request, checked whether the 5 triggers with no nearby
zeev-device speech (Sep 13 19:47:54, Sep 14 20:34:56, Sep 16 00:19:36, Sep 17
22:24:15, Sep 17 23:00:55) could be attributed to `zeev-watch`'s `speak`
command (a separate process/service that drives the same speaker through
the same daemon and is invisible to `_face_state`, so `_wake_loop_oww`'s
mic-release gate cannot see it) or to anything else observable in the
journals.

**`journalctl -u zeev-watch`, ±60s around all 5 timestamps: zero entries in
every window.** The dog-soothe/watch path was not active for any of these
5 triggers — that specific alternate mechanism is ruled out for this data.

**Widened `zeev-device` context (2 min before each trigger) and the
post-trigger transcript for each**, from `h3work/zoom_all.txt` and
`h3work/pi_zoom_2224.sh`/`pi_zoom_2300.sh` output:

| timestamp | model/score | Zeev speech in prior ~2min? | post-trigger transcript (characterized; verbatim redacted for the public repo) |
|---|---|---|---|
| Sep 13 19:47:54 | hey_sarina 0.97 | no | a coherent sentence about the dog — real speech (verbatim redacted) |
| Sep 14 20:34:56 | hey_zeev 0.93 | no | garbled but speech-shaped (verbatim redacted) |
| Sep 16 00:19:36 | hey_zeev 0.85 | no | "Ct" — near-nonsense, 1 syllable |
| Sep 17 22:24:15 | hey_zeev 0.86 | no | ~50-word coherent personal ramble — clearly real captured human speech (verbatim redacted) |
| Sep 17 23:00:55 | hey_zeev 0.94 | **yes** — two-voice goodnight announcement ended ~23s earlier (`speak_sync`/`eq_levels` activity 23:00:11–23:00:32 in `zeev-audio`) | garbled, hallucinated-sounding name (verbatim redacted)ing |

**Observables across the 5:**
- **Model skew**: 4 of 5 are `hey_zeev`, only 1 `hey_sarina`.
- **Time-of-day skew**: all 5 land evening/night (19:47–23:00) or just past
  midnight (00:19) — none in daytime hours. This is consistent with
  ambient household/TV noise patterns as plausibly as with anything
  Zeev-specific, and this task's harness has no way to distinguish those
  from here.
- **Score range**: 0.85–0.97, i.e. both just-over-threshold and
  comfortably-over-threshold triggers are represented; no clean cutoff.
- **Only 1 of 5 has any Zeev/Sarina speech logged nearby at all**, and even
  that one trailing (23:00:55) sits 23–48s after the goodnight
  announcement ended — far outside the ~1.5s `OWW_SETTLE` window the code
  uses for the one self-trigger path it explicitly guards against. Two of
  the five (19:47:54, 22:24:15) captured long, coherent, real human speech
  immediately after triggering, which reads as genuine ambient
  conversation having been picked up — plausibly genuine wakes (or at
  minimum, not spurious in the "nothing was said" sense) rather than false
  positives at all.

**Conclusion: unattributable from the available logs.** None of the 5 has
a zeev-device or zeev-watch speech event positioned where the code's own
self-trigger mechanism would need one to be (either during, per the mic-
release gate, or within ~1.5s after, per `OWW_SETTLE`). This is consistent
with — but does not further strengthen beyond — the main REFUTED verdict;
it does not open a new lead specific to H3. The skew toward evening/night
and toward `hey_zeev` may be worth a sibling hypothesis's attention (TV/
ambient-noise timing, or per-model threshold miscalibration — two of the
five sit right at 0.85–0.86 against an 0.84 floor), but that is outside
this task's lane.

**What would settle this going forward**: trigger-audio capture shipped to
the Pi today (09:18) — every future trigger now saves 3s of the causing
audio plus per-model scores to `~/Zeev/zeev/data/wake_captures/`. Once a
few more triggers accumulate, those clips become directly listenable,
which is the only way to move any of this from "unattributable" to a real
answer, for H3 or any sibling hypothesis.

**Corpus note**: the control corpus grew to 42.0 minutes total during this
task (coordinator-confirmed peaks: 0.0426 hey_zeev / 0.0155 hey_sarina —
consistent with the pooled figures reported above from the 12-minute
subset available when this harness ran); the TTS-vs-control comparison
holds against the larger corpus.
