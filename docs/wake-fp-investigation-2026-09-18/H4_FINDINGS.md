# H4: Is the decision rule wrong (single-frame trigger vs. sustained-run debounce)?

**VERDICT: INCONCLUSIVE** — not enough triggering events in the available
corpus to build or test a run-length histogram. This is a data-scarcity
finding, not a refutation or confirmation of the hypothesis.

## What was measured

Read the real production code first (`zeev/zeev.py`):

- `oww_best()` (`zeev/zeev.py:232`) fires the instant **any single 80ms
  frame** scores `>= oww_threshold(name)` for a given model. There is no
  debounce, no consecutive-frame requirement, no rolling-mean smoothing —
  confirmed by reading the function body, not inferred.
- `_wake_loop_oww()` (`zeev/zeev.py:16168`) calls `oww_best(scores)` once
  per 80ms frame (`model.predict(frame)`), and triggers `_wake_dispatch`
  on the very first frame that clears threshold. This confirms H4's
  premise about the current rule is correct: it is exactly a single-frame
  threshold, not a sustained-run requirement.

Verified live production thresholds on the Pi (bare `grep`, no `.env` dump):
```
OWW_THRESHOLDS=hey_zeev:0.84,hey_sarina:0.95
```
matches project memory.

Built `trace.py` to feed each 10-16kHz-mono-S16LE negative-corpus WAV
through `openwakeword.model.Model` frame-by-frame (1280 samples / 80ms,
matching production exactly — a continuous rolling-buffer feed, no resets,
since the corpus is continuous non-triggering speech and production's
energy-gate reset logic only matters across silence gaps, not within
continuous speech) and dump per-frame `{stem: score}` for both `hey_zeev`
and `hey_sarina`.

**Determinism**: ran the trace 3x on the same file (`neg_2.wav`). All
1501-frame score arrays were bit-identical across all 3 runs
(`run1==run2: True`, `run1==run3: True`). The measurement is stable.

## Corpus actually available this session

The shared negative corpus (`/home/azaurov/homelab-ops/wake-lab/corpus/`)
was thin for most of this session — the first fetch URL was a dead
livestream, so only a 120s clip (`neg_2.wav`) was present when work began.
Two more files landed during the session (a 10-min BBC-news-search hit,
`neg_raw_00001.wav`, and a 10-min tech-podcast-search hit which I
resampled myself from the raw 48kHz stereo download rather than wait for
the shared pipeline's own ffmpeg step — read-only operation on my own copy,
did not touch the shared corpus directory's naming). Two further
downloads (documentary, radio talk show) were still in flight when this
report was finalized and are not included.

**Total corpus analyzed: 22.0 minutes, 3 distinct sources (short misc
clip, ~10min news broadcast, ~10min tech podcast), 16,499 frames per stem.**

## Result: essentially zero activation anywhere in this corpus

Frame-level score distribution across the full 22-minute corpus:

| stem | n_frames | max score | mean score |
|---|---|---|---|
| hey_zeev | 16,499 | 0.0426 | 0.000044 |
| hey_sarina | 16,499 | 0.0135 | 0.000037 |

Threshold-crossing event counts (maximal consecutive-frame runs `>= T`),
swept from the live production threshold down to 60-80x below it:

```
hey_zeev  (live T=0.84):  0 events
          T=0.50:         0 events
          T=0.30:         0 events
          T=0.20:         0 events
          T=0.10:         0 events
          T=0.05:         0 events   <- still 16.8x below live threshold
          T=0.02:         2 events, run-lengths [1, 2]
          T=0.01:         4 events, run-lengths [1, 2, 2, 1]

hey_sarina (live T=0.95): 0 events
           T=0.50:        0 events
           T=0.30:        0 events
           T=0.20:        0 events
           T=0.10:        0 events
           T=0.05:        0 events   <- still 19x below live threshold
           T=0.02:        0 events
           T=0.01:        3 events, run-lengths [1, 1, 1]
```

Real excerpts of the only activity found (frame index, 80ms/frame,
window of raw scores around the crossing):

```
hey_zeev  event @frame 7845 len=1 peak=0.0349
  window = [0.0004, 0.0009, 0.0009, 0.0067, 0.0349, 0.0006, 0.0001, 0.0, 0.0]
hey_zeev  event @frame 8854 len=2 peak=0.0426
  window = [0.0006, 0.0001, 0.0002, 0.0059, 0.0426, 0.0296, 0.0037, 0.0039, 0.0057, 0.0131]
hey_sarina event @frame 6076 len=1 peak=0.0135
  window = [0.0001, 0.0001, 0.0004, 0.0035, 0.0135, 0.0075, 0.0012, 0.0002, 0.0]
```

Every one of these excerpts rises from near-zero to peak in exactly one
frame and decays back to near-zero within 1-2 frames — none is a
multi-frame plateau. That shape is *directionally* consistent with H4's
premise (activation is transient, not sustained), but the peaks here are
20-95x below the real production thresholds (0.0426 vs. 0.84 for
hey_zeev; 0.0135 vs. 0.95 for hey_sarina). **This is not evidence about
real false positives** — it is evidence about the model's response to
ordinary non-matching broadcast speech in general, which is a different
and much weaker claim.

Sanity check: fed 200 frames of synthetic white noise through both
models — max scores were 0.00019 (hey_zeev) and 0.00133 (hey_sarina),
confirming the near-zero baseline in the real corpus is a genuine "no
matching content" signal, not a measurement bug (e.g. wrong sample
format, broken preprocessing).

## Why this is INCONCLUSIVE, not REFUTED

The hard rule for this task states the measurement is "for every threshold
crossing in the negative corpus, record the run length" and "build a
histogram of run lengths for false positives." **There are no threshold
crossings at the live thresholds anywhere in the available 22-minute
corpus for either stem** — so there is no run-length histogram to build
at the scale that matters, and no basis to evaluate whether a
consecutive-frame or rolling-mean rule would separate false positives
from genuine wakes.

This null result is fully consistent with the documented real-world rate
(~1.4 false wakes/hour): the expected number of false-positive events in
a random 22-minute sample at that rate is ~0.5. Seeing zero is unsurprising
given the sample size, not evidence that the corpus or tooling is broken
(confirmed further by the white-noise sanity check and the presence of
real, if tiny, sub-threshold activity that behaves exactly as expected —
sharp transient bumps tied to specific moments in the audio).

**What this rules out**: it is not the case that ordinary broadcast
speech/podcast content routinely nudges these two custom-trained models
into the 0.05-0.84 range. Whatever content actually causes the documented
household false positives (TV audio, ambient speech scoring 0.63-0.92 per
project memory) is evidently a narrower, rarer phonetic collision than
"generic fluent English speech" — not something a random 22-minute sample
of news/podcast audio is likely to contain.

**What tomorrow's data would need to look like to resolve H4** (the two
paths forward, neither achieved tonight):
1. **Either** a much larger/targeted negative corpus (hours, not minutes
   — at 1.4 FP/hour a statistically useful sample of, say, 20 false-
   positive events needs on the order of 14+ hours of realistic household
   ambient/TV audio, not generic downloaded news), so real near-threshold
   crossings actually occur in the replay and can be histogrammed;
2. **or** direct instrumentation of the live device: production currently
   only logs the *triggering* score on a fire (`print(f"[wake] {best_name
   or label} trigger (score {score:.2f})"...)`, `zeev/zeev.py:16305`), not
   a per-frame trace leading up to it — so past false positives cannot be
   replayed for run-length shape after the fact. A temporary, low-cost
   instrumentation change (log the last N frame scores in a ring buffer
   whenever a trigger fires, for both false and genuine wakes) would let
   the *actual* household false-positive events tomorrow answer this
   question directly, which is strictly better evidence than any generic
   downloaded corpus.

## The asymmetry (hard rule 5)

No genuine-wake corpus was available tonight (real wakes are being
captured separately, over the next day). Even if the negative-corpus
evidence above had shown a clean "false positives are always length-1,
would-be genuine wakes are always length-3+" split, that alone would
**not** justify shipping a debounce: a debounce that delays or drops a
genuine wake is exactly as costly as one it lets through, and this session
has zero data on how long a genuine `hey_zeev`/`hey_sarina` utterance's
score stays above 0.84 for real. Given that no clean split was even found
here, this caveat is moot for tonight's specific verdict, but it remains
the binding constraint on ever accepting H4:

**If tomorrow's genuine-wake captures show real wakes reliably holding
`>= 0.84` (or `>= 0.95` for hey_sarina) for at least 2 consecutive 80ms
frames (160ms), with no or very few genuine wakes at exactly 1 frame,
AND a larger negative corpus (hours) shows real false positives are
predominantly 1-frame spikes at these same thresholds — only then would
a `("consec", 2)` rule be evidence-backed.** Absent both, no threshold or
debounce change should be deployed on this hypothesis.

## Positive control (instrument validation)

Before trusting a corpus max of 0.04 as "this content doesn't excite the
model" rather than "the replay harness is broken," synthesized `"Hey
Zeev"` and `"Hey Sarina"` via the bosgame Kokoro TTS server
(`http://localhost:5600/tts`, voices `am_adam`/`af_heart`), resampled to
16kHz mono S16LE, and ran them through the identical `trace.py` pipeline:

```
hey_zeev  TTS clip: max score 0.178  (own threshold 0.84 -- did not fire)
hey_sarina TTS clip: max score 0.998 (own threshold 0.95 -- fired, and
    SUSTAINED: 3 consecutive 80ms frames >= 0.95 -- [0.942, 0.998, 0.974])
```

This confirms two things: (1) the harness is not capped or broken — it
can and does register both a near-1.0 score and a genuine multi-frame
plateau when given matching content, so the corpus's near-zero readings
are a property of the corpus content, not the instrument; (2) the one
clean high-scoring example available tonight (synthetic TTS, not a real
household wake — explicitly not being overclaimed as a genuine wake) is
at least an existence proof that "sustained multi-frame" is achievable in
this pipeline, which is what H4 predicts for real genuine wakes. `hey_zeev`
not firing on this particular TTS rendering is expected and unremarkable
(different voice/prosody than the household's per-model training data)
and not treated as a negative result about the model.

Caveat on the replay methodology itself: `trace.py`'s continuous no-reset
frame feed does not reproduce one piece of production behavior —
`_wake_loop_oww` (`zeev/zeev.py` ~lines 16280-16290) resets the model and
replays a pre-roll on every speech-onset transition (energy gate `hold`
elapsing then `rms >= gate`), which happens roughly every ~2s during
continuous loud speech such as a podcast. The replay here never resets,
so any false positive that is specifically a post-reset transient artifact
would not appear in this corpus's trace even if it occurs on the real
device. Not fixed tonight (verdict is INCONCLUSIVE regardless of this
gap), but worth fixing if a future negative-corpus replay effort resumes.

## No code change made

Because the verdict is INCONCLUSIVE (not CONFIRMED), no candidate diff to
`zeev/zeev.py` is included — hard rule 4 (no deploys) and the task's own
instruction ("If CONFIRMED, a minimal candidate diff...") both gate a
production change on confirmation this session did not reach. The
`oww_best()`/`_wake_loop_oww()` call sites are already identified above
(`zeev/zeev.py:232`, `zeev/zeev.py:16168`, `zeev/zeev.py:16301`) for
whoever picks this up once real crossing data exists.

## Artifacts in this directory

- `trace.py` — per-frame score dumper (openWakeWord replay, matches
  production frame feed exactly)
- `analyze.py` / `aggregate.py` — run-length histogram + candidate-rule
  elimination-count tooling (ready to use once a corpus with real
  near-threshold crossings exists; produced 0-event outputs against
  tonight's corpus, included as `aggregate_output.txt`)
- `multithresh_output.txt` / `multithresh_output3.txt` — the full
  threshold-sweep runs behind the tables above (2-file and 3-file corpus
  respectively)
- `neg_2.run1.json`, `neg_raw_00001.run1.json`, `neg_more_00001.run1.json`
  — raw per-frame score traces for the 3 corpus files analyzed (reusable
  without re-running inference)
- `analyze_captures.py` / `test_analyze_captures.py` — the tool built for
  tomorrow's real data, see below.

---

## What runs tomorrow: analysis against real `wake_capture_save()` clips

Real per-trigger audio capture shipped to the Pi at 09:18 today
(`a4923595`, `wake_capture_save()` in `zeev/zeev.py`) and went live —
**zero captures exist as of this writing**; they accumulate through
normal household use starting today. This section documents the tool
built to analyze them once they exist. It does **not** change the
verdict above, which remains INCONCLUSIVE.

### Capture format (read from the real implementation, not guessed)

Every trigger — genuine and false alike, deliberately — writes a
`<stamp>-<fired>.wav` (3.0s, 16kHz mono S16LE, the ring buffer as it
stood at trigger time, including frames the energy gate skipped) plus a
`<stamp>-<fired>.json` sidecar:

```json
{
  "ts": 1234567890.1, "iso": "...", "fired": "hey_zeev",
  "score": 0.91, "scores": {"hey_zeev": 0.91, "hey_sarina": 0.02},
  "thresholds": {"hey_zeev": 0.84, "hey_sarina": 0.95},
  "energy_gate": true, "gate_level": 512.3, "seconds": 3.0,
  "label": null
}
```

`label` is `null` until a human (or `wake_harvest.py`) fills it in. The
label vocabulary that tool will use isn't fixed yet, so `analyze_captures.py`
accepts several plausible spellings (`false`/`false_wake`/`fp` →
`"false"`; `genuine`/`true`/`tp`/`real` → `"genuine"`) and buckets
anything else as `other:<value>` rather than silently dropping it —
an unrecognized label should be visible as a signal to update this
script's vocabulary, not swallowed.

### The tool: `analyze_captures.py`

```
python3 analyze_captures.py ~/Zeev/zeev/data/wake_captures
```

Walks the directory, and for every `.wav`+`.json` pair: runs the clip
frame-by-frame through the real models (`real_score_fn`, same 1280-sample/
80ms frame feed as `trace.py`), extracts only the **fired model's own
score curve** (a capture only tells you about the model that actually
triggered it), computes `max_run_length()` at that model's live threshold
(`meta["thresholds"][fired]`, read from the capture itself rather than a
hardcoded constant, so it stays correct if thresholds change), buckets by
label, and prints a run-length histogram + median/mean per bucket. This
IS the H4 test: false-wake run-lengths vs. genuine-wake run-lengths,
compared directly, at real thresholds.

`analyze_captures()` is factored to take a `score_fn(wav_path, stems) ->
list[{stem: score}]` parameter so the directory-walking/labelling/
histogram logic is fully testable without real model inference (see next
section) — production's `real_score_fn()` is the only piece that needs
`openwakeword`/`numpy` installed.

### Acceptance criterion, fixed BEFORE any real data exists

Stated now, in writing, so it cannot be fitted to whatever the data turns
out to show:

- **Minimum sample size: at least 15 labelled false wakes AND at least 15
  labelled genuine wakes.** Below that, `compare_populations()` reports
  "still INCONCLUSIVE -- not enough labelled data yet" regardless of what
  the 15-or-fewer available points look like. This is a low bar (a rough
  first read, not a rigorous statistical test) chosen to be reachable in
  about a day of normal household use given the documented ~1.4 false
  wakes/hour and typical daily genuine-wake volume, while still being
  more than the 1-3 point samples that would make a median meaningless.
- **CONFIRMED** if `median(max_run_length)` for genuine wakes is `>= 3`
  frames (>= 240ms sustained above threshold) AND `median(max_run_length)`
  for false wakes is `<= 1` frame (single 80ms spike). This specific gap
  (3 vs 1) is chosen because a real wake phrase is acoustically ~500-800ms
  and should plausibly span several 80ms scoring frames if the model is
  well-calibrated to it, while H4's premise is that a false positive is a
  one-frame coincidental spike.
- **REFUTED** if either: (a) false wakes are themselves commonly sustained
  (`median >= 3` for false wakes too — meaning "transient spike" is not
  actually what separates a false positive), or (b) a large share
  (`>= 30%`) of genuine wakes are themselves single-frame
  (`max_run_length <= 1`) at the live threshold — meaning a
  `("consec", 2)` or similar debounce would also delay/drop real wakes,
  which is exactly the cost hard rule 5 warns about.
- **Otherwise (data exists, populations overlap, neither clean pattern
  holds): still INCONCLUSIVE.** `compare_populations()` implements exactly
  this three-way branch (see `analyze_captures.py`), not a two-way
  confirmed/refuted split, because "we have data but it doesn't separate
  cleanly" is a real, distinct outcome from "not enough data yet."

### End-to-end self-test, run tonight against synthetic data (no real captures needed)

`test_analyze_captures.py` builds real `.wav`+`.json` capture pairs in a
temp directory, in the exact `wake_capture_save()` format, with HAND-PICKED
KNOWN run lengths (e.g. 5 synthetic "false" captures each scripted to a
single-frame spike, 5 synthetic "genuine" captures each scripted to a
4-frame plateau), and a stub `score_fn` that returns those exact
prescribed scores instead of running real model inference on the (silent,
placeholder) WAV content. This validates the full path — file discovery,
JSON parsing, label bucketing, run-length extraction, histogram
construction, and the acceptance-criterion branching itself — end to end,
deterministically, offline:

```
$ python3 -m unittest test_analyze_captures -v
test_acceptance_criterion_confirms_on_clean_separation ... ok
test_acceptance_criterion_inconclusive_below_sample_size ... ok
test_acceptance_criterion_refutes_when_genuine_also_transient ... ok
test_separates_transient_false_from_sustained_genuine ... ok
test_bucket_label ... ok
test_eval_rule_consec ... ok
test_max_run_length_pure ... ok
----------------------------------------------------------------------
Ran 7 tests in 0.267s
OK
```

Three of these directly exercise the acceptance criterion itself: one
constructs a clean separation and asserts the tool prints CONFIRMED, one
constructs genuine wakes that are themselves mostly single-frame and
asserts REFUTED, and one constructs too few labelled points and asserts
INCONCLUSIVE — so the criterion's own logic is proven correct before it
ever sees real data, not just asserted in prose.

What this self-test does **not** prove: that real openWakeWord inference
on real captured audio will behave as expected. That was checked
separately (the positive-control section above, and the 22-minute
negative-corpus replay) using the real `real_score_fn()` path, not the
stub. The stub's only job is to isolate and prove the analysis logic;
real evidence for H4 itself still requires real labelled captures, which
do not exist yet. **Say this plainly: nothing in this session confirms or
refutes H4 with real device data. That test starts tomorrow, once
captures accumulate and get labelled.**
