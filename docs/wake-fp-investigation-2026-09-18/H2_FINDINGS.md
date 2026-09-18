# H2: Does the energy gate manufacture false-positive wake scores?

## Verdict

**CONFIRMED at the mechanism level, at sub-threshold magnitudes.
INCONCLUSIVE on whether it causes real false wakes** — the corpus never
gets close enough to threshold, under either scoring mode, to show that.

The `model.reset()` + pre-roll-replay stitching in `_wake_loop_oww`'s
energy gate provably perturbs `hey_zeev` scores relative to continuous
scoring, and the perturbation is confined almost exactly to the claim in
H2: it appears **only** in the first 0-5 frames after a reset and decays
to bit-for-bit identical with continuous scoring by frame 6, on every one
of 900+ resets checked across three gate-aggressiveness settings. That is
a clean, repeatable signature of an artifact from the stitching, not
noise. But the magnitude of that artifact on this corpus is 3-4 orders of
magnitude below the gap to any plausible threshold (max elevation
+0.0003, against thresholds of 0.5-0.84), so it cannot be shown to
"manufacture" an actual false wake from this evidence alone. A second,
opposite-direction effect was also found and is arguably more important
operationally: the same mechanism can **suppress** a genuine score peak by
a large fraction (one case: 0.0426 -> 0.0144, a 66% cut) right at a
reset, which cuts against real-wake reliability rather than causing false
ones.

## Method

Faithful reimplementation of `_wake_loop_oww`'s energy-gate logic, read
directly from `zeev/zeev.py` (`_wake_loop_oww`, energy-gate block around
lines 16168-16300 as of this run) — `levels` deque (maxlen 80, ~6s of
frame RMS), gate recomputed every 40 frames as `max(median(levels) *
OWW_ENERGY_MULT, OWW_ENERGY_MIN)`, `model.reset()` + full pre-roll replay
(`OWW_PREROLL=20` frames) on onset, `hold=OWW_HOLD_FRAMES=25` frames of
uninterrupted scoring after onset before the gate can re-evaluate. Code:
`wake_h2/harness2.py` (`run_continuous` = A, `run_gated` = B — reads
frame-by-frame, byte-identical constants to the production code).

- Model: `hey_zeev.onnx` via openWakeWord 0.4.0 (`wake-lab/venv`),
  `Model(wakeword_model_paths=[...])`, deterministic (verified: 3 repeat
  runs at each `OWW_ENERGY_MULT` produced bit-identical `b_scores` lists).
- Corpus: final state, 5 files, 42.0 minutes, ASCII confirmed 16kHz mono
  S16LE for all 5 (`/home/azaurov/homelab-ops/wake-lab/corpus/neg_*.wav`).
  Real speech (podcast/documentary/talk-show/raw-download negatives), no
  wake phrase.
- A = every frame scored, single continuous buffer, no reset, ever.
- B = the gate reimplementation above, run separately per
  `OWW_ENERGY_MULT` in {1.5, 1.8, 2.5}.
- All 5 files run through both A and B at each mult; per-frame score
  pairs collected only where B actually scored (frames the gate skips
  have no B counterpart to compare).

## Step 1: crossing-based test (as originally specified) — dead on this corpus

Max score anywhere in the whole 42-minute corpus, either scoring mode,
either stem: 0.0426 (`hey_zeev`, in `neg_raw_00001.wav`, continuous
scoring). Live thresholds are 0.84 (`hey_zeev`) / 0.95 (`hey_sarina`);
even the generic default (0.5) is never approached. Zero threshold
crossings in A, zero in B, at every threshold checked down to 0.02. The
crossing-set comparison the task originally specified (`A crossings ==
B crossings` -> REFUTED) is **vacuously true** here — there is nothing to
cross under either condition — so it is not usable as evidence and is
reported only for completeness, not as the verdict basis.

## Step 2: score-delta distribution (the real test)

Delta = B score minus A score, at the same audio frame, restricted to
frames B actually scored (the rest have no B value to diff).

| mult | scored frames | resets | mean Δ | p95 Δ | max Δ | min Δ | %B>A | %B<A |
|---|---|---|---|---|---|---|---|---|
| 1.5 | 23,364 | 900 | 0.000000 | 0.000000 | +0.000304 | -0.000412 | 7.0% | 6.9% |
| 1.8 | 19,169 | 738 | -0.000002 | 0.000000 | +0.000130 | **-0.028150** | 6.6% | 7.1% |
| 2.5 | 9,497 | 366 | 0.000000 | 0.000000 | +0.000234 | -0.000200 | 7.1% | 6.6% |

86% of scored frames have exactly zero delta (B is numerically identical
to A on those frames — expected, since most scored frames are deep inside
a `hold` window, far from the last reset, where the model's rolling
buffer has long since re-converged).

## Step 3: does the delta concentrate right after a reset? Yes — cleanly.

Bucketed by frames-elapsed-since-the-last-reset, *at the scored frame*:

**mult=1.8** (738 resets):
```
0-5   : n=4428   mean=-0.000008  p95=+0.000001  max=+0.000130
6-20  : n=11056  mean= 0.000000  p95= 0.000000  max= 0.000000
21+   : n=3685   mean= 0.000000  p95= 0.000000  max= 0.000000
```

**mult=1.5** (900 resets):
```
0-5   : n=5400   mean= 0.000000  p95=+0.000001  max=+0.000304
6-20  : n=13479  mean= 0.000000  p95= 0.000000  max= 0.000000
21+   : n=4485   mean= 0.000000  p95= 0.000000  max= 0.000000
```

**mult=2.5** (366 resets):
```
0-5   : n=2196   mean= 0.000000  p95=+0.000002  max=+0.000234
6-20  : n=5476   mean= 0.000000  p95= 0.000000  max= 0.000000
21+   : n=1825   mean= 0.000000  p95= 0.000000  max= 0.000000
```

At all three mults, **every single frame in the 6-20 and 21+ buckets has
delta exactly 0.000000** — no exceptions, thousands of frames each
bucket. All non-zero deltas, positive or negative, land in the 0-5
bucket. That is the decay curve the task asked for, and it's about as
clean as this kind of measurement gets: within ~5 frames (~0.4s) of
`model.reset()` + pre-roll replay, the gated model's rolling buffer has
fully re-converged to what continuous scoring would show, and stays
converged for the rest of the hold window and beyond.

Within the 0-5 bucket itself, the effect is not one-directional — it's
roughly a coin flip in the tested audio:

| mult | 0-5 n | positive | negative | exactly zero |
|---|---|---|---|---|
| 1.5 | 5400 | 1631 (30%) | 1601 (30%) | 2168 (40%) |
| 1.8 | 4428 | 1262 (28%) | 1354 (31%) | 1812 (41%) |
| 2.5 | 2196 | 676 (31%) | 629 (29%) | 891 (41%) |

So the mechanism does elevate scores sometimes, exactly where H2
predicts — but on this corpus it suppresses about as often as it
elevates, and both effects are tiny.

Sanity check against a confound: mean *continuous* (A) score is not
systematically different between buckets (0.000032-0.000058 across all
three), so the 0-5 bucket isn't just landing on louder/quieter audio by
chance — the concentration of delta there is attributable to the reset
mechanism, not to which parts of the corpus happen to trigger resets.

## A found effect bigger than the elevation, in the other direction

The single largest delta in the whole sweep is **-0.028150** (mult=1.8,
`neg_raw_00001.wav`, frame 7353) — not an elevation, a suppression. That
frame is the exact global peak of the entire corpus: continuous scoring
(A) reaches 0.0426 there (the highest score found anywhere, either mode,
either mult), but gated scoring (B) only reaches 0.0144 at the same
frame, because a `model.reset()` fires at `since_reset=0` on that exact
frame — the onset detector catches the same speech, but the fresh
buffer + 20-frame pre-roll hasn't reconstructed as much context as
continuous accumulation had by that point in the utterance, so the peak
is cut by 66%. This did not reproduce at mult=1.5 or 2.5 (max negative
delta there was -0.0004 and -0.0002 respectively) — it's a
timing-alignment coincidence (whether a reset happens to land exactly on
the loudest moment of an utterance), not a scaling property of
`OWW_ENERGY_MULT`. It matters because it's evidence the gate can cost a
**genuine** wake some of its margin right when reliability matters most,
which is the opposite failure mode from H2's claim but comes from the
same mechanism and is worth flagging for whoever owns the "does the gate
ever eat real wakes" question.

## What `OWW_ENERGY_MULT` sweep shows

More aggressive gating (mult=1.5, more resets: 900) does **not** produce
a larger per-reset delta than looser gating (mult=2.5, fewer resets:
366) — max elevation is the same order of magnitude at all three settings
(+0.0003 / +0.00013 / +0.00023). What scales with resets is the *number*
of frames exposed to the effect (5400 vs 4428 vs 2196 in the 0-5 bucket),
not the size of the effect per reset. This matches the mechanism's own
logic: the pre-roll always replays the same fixed 20 frames regardless of
`OWW_ENERGY_MULT`, so the magnitude of buffer-reconstruction error at
onset shouldn't depend on how sensitive the gate is — only on how well
those 20 pre-roll frames happen to reconstruct that particular attack.

## What this does NOT establish

- **No genuine-wake corpus was available tonight.** Every measurement
  above is on negative (non-wake) speech. This shows the *mechanism*
  perturbs scores near a reset; it says nothing about whether disabling
  or changing the gate would preserve real "hey Zeev" wakes better or
  worse. The 66%-suppression finding above is a hint that it could go
  either way on a real utterance, not a demonstrated fix target.
- **This corpus cannot show real false wakes are caused by this
  mechanism.** Scores never get within ~15-20x of threshold under either
  scoring mode. The reported symptom (~1.4 false wakes/hour on TV
  audio/ambient speech) must involve audio that scores far higher than
  anything in this 42-minute sample — TV dialogue or ambient speech that
  is phonetically closer to "hey Zeev" than generic podcast/documentary
  narration. For H2 to explain a *real* false wake, the same reset-time
  perturbation (which here tops out at +0.0003 absolute) would need to
  occur on audio where the continuous score is already close to
  threshold (e.g. 0.6-0.8), where even a small proportional boost from a
  botched buffer reconstruction could matter more than it does at these
  near-zero baseline scores. That scenario is untested — this corpus
  doesn't contain it.
- **The gate exists for a real, measured reason** — it cut the Pi from
  66.6C to ~60C by skipping ONNX inference on ~85% of frames in this
  corpus (86% of scored-vs-seen ratios above are actually inverted —
  e.g. mult=1.8 scored only 494-5512 of 1501-7499 frames per file,
  roughly a third). "Just turn it off" is not free; it's a thermal
  regression on hardware that's already tight (Pi Zero 2W, one core,
  ~200MB headroom per CLAUDE.md).

## What to look for once real trigger captures land

`~/Zeev/zeev/data/wake_captures/` sidecars (per-model scores, thresholds,
`energy_gate`, `gate_level`, 3s ring buffer) went live today; zero
captures so far. Once real false triggers accumulate, the direct test
this corpus couldn't provide:

1. **Replay each captured 3s ring buffer through this same harness**
   (`run_continuous` vs `run_gated`, faithful gate reimplementation) and
   compare to the score the sidecar actually recorded at trigger time.
   - If continuous scoring (A) *also* crosses the live threshold on the
     same audio -> the gate isn't the cause of that particular false
     wake; it would have fired regardless.
   - If continuous scoring does **not** cross but the sidecar's recorded
     (gated) score did -> that is a direct, full-magnitude, real-world
     confirmation of H2, not just a sub-threshold mechanism demo.
2. **Check `since_reset` at the trigger frame** (reconstructible from the
   ring buffer + `gate_level`/RMS even though the sidecar doesn't log it
   directly): does the false trigger fall inside the first 0-5 frames
   after an onset, matching the decay window found here? If false triggers
   cluster there disproportionately (vs. deep in a hold window or a
   continuously-open gate), that's corroborating structural evidence.
3. **Watch `gate_level` for staleness** as an alternative/confounding
   explanation independent of H2: a `gate_level` computed from a quiet
   preceding 6s window that's stale relative to a sudden loud TV/ambient
   segment would let a lot more raw audio through the onset check without
   any stitching artifact needed — rule this out before attributing a
   real trigger to the reset mechanism specifically.
4. **Compare `energy_gate: true` vs any captures with the gate off**, if
   both ever exist — the cleanest possible confirmation is the same
   audio/config falsely triggering with the gate on and not with it off
   (or vice versa for the suppression direction).

## Minimal candidate diff (precautionary, NOT validated against real triggers)

Given the mechanism is confirmed real but its measured magnitude here is
far too small to explain the actual symptom, this is offered as a
low-risk, cheap-to-test candidate — not a claimed fix. It follows
directly from the decay curve: don't let a threshold crossing count
during the exact window (frames 0-5 post-reset) where the score is shown
to be least trustworthy relative to continuous scoring, in either
direction.

```diff
--- a/zeev/zeev.py
+++ b/zeev/zeev.py
@@ (in _wake_loop_oww, energy gate block)
+                    # H2 finding (wake-lab, 2026-09-18): the reset+preroll
+                    # splice measurably diverges from continuous scoring
+                    # only in the first ~5 frames after model.reset() (both
+                    # elevated and suppressed cases seen; decays to exact
+                    # equality with continuous scoring by frame 6 across
+                    # 900+ resets, 3 OWW_ENERGY_MULT settings, 42min corpus
+                    # -- see wake_h2/H2_FINDINGS.md). Require a few frames
+                    # of post-reset settle before a crossing counts, same
+                    # spirit as OWW_HOLD_FRAMES already giving the buffer
+                    # time to fill, just narrower.
                     scored += 1
                     scores = model.predict(frame)
+                    settle_frames = 5
+                    since_last_reset = hold_frames_used_since_reset  # needs threading through
```

This is intentionally left as a sketch, not a finished patch — the real
code doesn't currently track "frames since reset" at the trigger-check
call site (only `hold` counts down), and wiring that through needs a
decision about whether `OWW_HOLD_FRAMES` (25) already covers this or a
separate narrower counter is worth the added state. **Evidence needed
before landing anything**: at least one real captured false trigger whose
`since_reset` position and rescored (continuous) trace show the
sub-threshold pattern found here reproducing at real trigger magnitude —
without that, this diff would be defending against an effect on the
order of 0.0003, three orders of magnitude below the 0.84 threshold, and
isn't justified by tonight's data alone.
