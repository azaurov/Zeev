# H1 Findings: does `hey_zeev.onnx` assign genuine-wake-range scores to ordinary fluent speech?

## Verdict: REFUTED (for the corpus tested; scope-limited — see §3)

H1 as stated — "`hey_zeev.onnx` assigns scores in the genuine-wake range (~0.63–0.92)
to ordinary fluent speech containing no wake phrase at all" — is **refuted** against
the negative corpus available tonight. The model was overwhelmingly inert on it, not
merely below threshold but near-zero.

## 1. Evidence

Corpus: 5 files, 2520.1s = **42.0 minutes** of downloaded news/talk/panel broadcast
speech, 16kHz mono, containing no wake phrase. 31,498 frames (80ms each) scored per
model, `model.reset()` before each file, via
`/home/azaurov/Zeev/.claude/worktrees/agent-a26eb500d80254992/wake_h1/measure.py`
(`run1.json`, run against the full and final corpus glob).

Aggregate, across all 31,498 frames:

| model | max | mean | >0.5 | >0.68 | >0.84 (live thr.) | >0.95 (live thr.) |
|---|---|---|---|---|---|---|
| hey_zeev | **0.04259** | 3.98e-05 | 0 | 0 | 0 | 0 |
| hey_sarina | **0.01545** | 5.48e-05 | 0 | 0 | 0 | 0 |

Live thresholds pulled directly from the Pi (`ssh ragnar@ragnarok "grep OWW_THRESHOLDS ~/Zeev/.env"`):
`hey_zeev:0.84, hey_sarina:0.95` — confirms the memory note, not just assumed.

Debounced threshold-crossing events (rising-edge, collapsing consecutive
above-threshold frames into one event) at the live thresholds: **0 for hey_zeev, 0
for hey_sarina**, across the whole corpus. `cooccurrence_events` is an empty list.

Per-file peaks (all far below even the loosest 0.5 histogram bucket, let alone
0.84/0.95):

| file | dur (s) | hey_zeev max | hey_sarina max |
|---|---|---|---|
| neg_2.wav | 120.1 | 0.0096 | 0.0123 |
| neg_more_00001_7ZhdXgRfxHI.wav | 600.0 | 0.0129 | 0.0043 |
| neg_more_00001_DFYaNjzI1aI.wav | 600.0 | 0.0095 | 0.0155 |
| neg_more_00001__6DwrTJ4eMQ.wav | 600.0 | 0.0172 | 0.0076 |
| neg_raw_00001.wav | 600.0 | 0.0426 | 0.0135 |

The single loudest 80ms moment across all 42 minutes of broadcast speech
(0.0426, hey_zeev) is about **1/20th** of the live trigger threshold (0.84).

**Independent cross-check**: the coordinator scored the same final corpus
independently and reported identical peaks (0.0426/0.0135 for `neg_raw_00001.wav`,
matching values for every other file) — this agent's own run1 reproduces those
numbers exactly. A sibling agent (H4) additionally confirmed bit-identical
per-frame output across repeated runs on the same file, which is expected for
deterministic ONNX CPU inference and is why a second/third full pass here would
not add information beyond confirming reproducibility already established
independently.

## 2. Co-occurrence sub-question (do hey_zeev / hey_sarina peak on the same moments?)

`cooccurrence_events` — built from overlapping threshold-crossing windows — is
**empty by construction**, because neither model ever crosses its live threshold
anywhere in this corpus. **This tells us nothing about whether the shared "Hey"
onset buys the two models separation** — there is no crossing on either side to
compare, so absence of co-occurring *events* is not evidence of separation or of
its absence. This sub-question is **unanswered by this corpus**, not resolved.

The per-frame time series were captured in `run1.json`'s histograms only (not the
raw arrays), so a proper sub-threshold correlation coefficient between the two
stems was not computed for run1. A dedicated script for this
(`/home/azaurov/Zeev/.claude/worktrees/agent-a26eb500d80254992/wake_h1/cooccur.py`)
was written but **not executed**, per the coordinator's explicit instruction to
stop before starting any new measurement. What can be said qualitatively from the
per-file peak table above: the two stems' peaks do not obviously track each other
file-to-file (e.g. `neg_more_00001_7ZhdXgRfxHI.wav` has hey_zeev's second-highest
peak but hey_sarina's lowest; `neg_raw_00001.wav` has hey_zeev's highest peak by a
wide margin while hey_sarina's peak there is unremarkable) — a rough visual
signal against strong correlation, but not a substitute for the actual
correlation coefficient, which remains unmeasured.

## 3. What this does NOT establish (scope limits)

- **Real false wakes happen at ~1.4/hour in the actual room** (documented rate).
  This corpus produced **zero** crossings in 42 minutes, and even at the real
  observed rate, 42 minutes predicts under one crossing (1.4/hr × 0.70hr ≈ 1),
  so a small residual chance of missing a real crossing in this exact sample size
  exists in principle — but the margin here isn't a near-miss, it's two full
  orders of magnitude (peaks ~0.04 vs. a 0.84 threshold), so sampling noise is not
  a plausible explanation for the gap. **Something clearly does activate hey_zeev
  in the real room; this corpus does not contain whatever that is.**
- **Not tested**: the actual microphone + Whisplay enclosure acoustic path (this
  was pure digital-audio replay of downloaded files, not a re-recording through
  the Pi's mic/ADC/enclosure resonance); real household TV audio/speakers/room
  acoustics (this corpus is generic downloaded BBC/NPR/panel content, not the
  specific TV/media actually playing in the house); any utterance phonetically
  close to "hey zeev" (names, filler words, other households members' phrasing)
  — the corpus was selected only for "no wake phrase," not for phonetic
  near-misses, which is the more likely source of the real 1.4/hr rate; and any
  music/singing/laughter/non-speech household audio.
- **No genuine-wake corpus was available tonight** (per task framing) — so this
  finding says nothing about whether any hypothetical fix (retraining, gating,
  etc.) would preserve real wakes. It only says this specific model is inert on
  this specific negative corpus.
- Determinism/stability: only run1 was completed to full JSON; runs 2 and 3 were
  started but killed mid-flight at the coordinator's explicit direction once
  independent cross-checks (coordinator's own scoring + sibling H4's
  bit-identical-repeat finding) made further full passes redundant. This agent's
  own run1 numbers match the coordinator's independently-computed numbers
  exactly, which is the corroboration in place of a 3rd from-scratch run here.

## 4. What would settle H1 properly

Real trigger-audio capture shipped to the Pi (2026-09-18, `wake_capture_save`,
3s ring buffer + per-model scores + thresholds on every trigger, saved to
`~/Zeev/zeev/data/wake_captures/`) is the right instrument, once populated:

- **Score the captured false-positive clips through this same offline replay
  pipeline** and compare against this corpus's near-zero baseline. If real
  false-positive captures also score near-threshold (0.6–0.9+) the way project
  memory records, that would show the model *does* separate ordinary broadcast
  speech from whatever it's actually triggering on — refuting the broad form of
  H1 ("fires on ordinary fluent speech generally") while leaving open a narrower
  H1′ ("fires on some specific phonetic pattern common in this household").
- **Compare the captured clips' spectral/phonetic content against this negative
  corpus** — if captures cluster around specific words/names/phrases (e.g. a
  household member's name, a common exclamation), that identifies the actual
  false-positive trigger class directly, which this corpus was never built to
  contain.
- **Re-run the co-occurrence question on real captures**: since real captures
  will include both models' saved scores per trigger, check directly whether
  hey_zeev and hey_sarina crossings coincide on genuine false-positive audio —
  this is the correct dataset for that question, not a corpus where neither
  model ever crosses at all.
- **Once enough real genuine-wake captures also accumulate**, compare
  false-positive-capture score distributions against genuine-wake-capture score
  distributions directly — this is the only way to test whether any proposed
  fix (retraining, a debounce, phonetic gating) would separate real wakes from
  real false positives, which is exactly the test this offline broadcast corpus
  cannot provide.
