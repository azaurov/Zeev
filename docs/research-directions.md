# Research directions (brainstorm, not committed work)

Speculative directions raised 2026-08-03, kept closer to what Zeev could
actually use than the from-scratch ML infra ideas that prompted them
(contrastive wake-word training, contrastive memory embeddings, CLIP-style
joint audio/vision encoding — all blocked on the same thing: no training
infra and no paired/labeled datasets exist anywhere in this project).

These four are ordered roughly by how close they are to buildable with what
already exists.

## 1. Retrieval-augmented personalization (no training needed) — DONE 2026-08-04

Semantic memory (`embed_text()` / `message_vecs`, see CLAUDE.md "History RAG")
already retrieves by embedding similarity using a frozen pretrained model.
Instead of fine-tuning that model, improve retrieval quality directly:
re-ranking, recency-weighting, per-topic clustering of past exchanges. Zero
new infrastructure — just better use of vectors already being computed.

Implemented in `retrieve_semantic()` and `retrieve_relevant()` (both in
`zeev/zeev.py`):

- **Recency-weighting**: `_recency_factor(ts)` returns an exponential decay
  (half-life `RAG_RECENCY_HALFLIFE_DAYS`, default 30) blended into ranking as
  `sim + RAG_RECENCY_WEIGHT * recency`. It's a *nudge*, not a filter —
  `min_sim`/`min_score` still gate on the raw similarity/keyword-overlap
  score, never the blended one, so recency can only reorder among
  already-relevant hits, never admit an irrelevant-but-recent one. Fails
  open (factor 1.0, no penalty) on a missing or unparseable timestamp.
- **Dedup re-ranking**: `retrieve_semantic` skips any candidate whose vector
  is a near-duplicate (cosine ≥ `RAG_DEDUP_SIM`, default 0.97) of a hit
  already picked, so asking the same question on two different days doesn't
  spend the whole `k` budget on the same answer twice.
- Per-topic clustering was considered and dropped for now — at the message
  counts this project actually has (hundreds, not millions), a clustering
  pass would add real complexity for a benefit the dedup pass already
  covers most of.

Tests: `tests/test_rag_reranking.py`.

## 2. Cross-modal grounding via the LLM as the shared space — DONE 2026-08-04

Rather than training a joint audio/vision encoder, use natural language as
the alignment layer that already exists: camera frames are described in text
via `vision_complete()`, and that text is already retrievable by the same
semantic-search path as everything else. This gets a rough multimodal
synergy for free — the alignment work is outsourced to a pretrained LLM
instead of trained from scratch.

The retrieval path already worked before this — a vision reply is just an
assistant message, embedded and RAG-able like any other. What was missing,
and is the actual content of this change, is that a resurfaced vision
description was indistinguishable from a spoken fact. That's this project's
most-repeated failure class (see the Wyze camera section of CLAUDE.md: a
stale or hallucinated room description read back as current truth).

Implemented (`zeev/zeev.py`):

- `finish_turn(..., vision=True)` and the two non-`finish_turn` vision call
  sites (web `/snap`, terminal `/look`) prefix the **DB-stored** copy of a
  vision-derived reply with `_VISION_TAG`. Only the stored copy — what's
  spoken and what's held in the live session (`ctx.session`) stays the plain
  reply, so nothing audible or immediately re-read by the LLM changes.
- `_build_system_prompt` strips the tag from any RAG hit that carries it and
  appends "(This was a camera observation from a past moment, not a
  standing fact — the room may look different now.)" — the model can still
  use the memory, just not as settled present-tense fact.
- Applied at all 6 places a vision reply reaches storage: the 4 device-mode
  `finish_turn` sites (local Pi camera, single named Wyze camera, "check all
  cameras" sweep, named-subject sweep — each only when a frame was actually
  inspected, not on a bare "couldn't get a picture") plus web `/snap` and
  terminal `/look`.

**Live-verified limitation, not a regression**: `retrieve_semantic` anchors
on the embedding of the *user's trigger phrase* ("check the basement cam"),
not the vision description itself (`message_vecs` is keyed by user-role
messages; see "History RAG" in CLAUDE.md). Measured live: a later question
phrased like the original trigger ("what did you see in the basement
earlier") scored 0.72 similarity and surfaced the hit; one phrased around
the *content* instead ("is the cat still on the couch") scored only 0.48,
below `min_sim` (0.55), and surfaced nothing. This ceiling predates this
change — it's true of the whole RAG path, for every kind of exchange, not
specific to vision — but it bounds how "grounded" this actually is: recall
depends on rephrasing the question, not just on the fact being true.
Embedding the assistant's reply as a second index would close this gap; out
of scope here.

Tests: `tests/test_vision_grounding.py`.

## 3. Few-shot wake-word personalization — DONE 2026-08-04

Instead of contrastive pretraining, a small per-household enrollment step:
record a handful of samples of Alex saying the wake phrase, then adjust
either the existing classifier's threshold or a lightweight per-user
embedding distance. A much smaller ML problem than full retraining, and
plausibly Pi-feasible where a from-scratch contrastive/CLIP model is not.

Went with the threshold path, not embedding distance: openWakeWord's
`Model.predict()` *is* the embedding-plus-classifier pipeline, and there's no
separate per-user embedding space to measure distance in without training
one — which is exactly the infra this direction exists to avoid. The
threshold path needed nothing new: `OWW_THRESHOLDS` (per-model overrides)
already existed for exactly this kind of tuning.

Implemented:

- `oww_score_pcm(model, pcm)` (`zeev/zeev.py`, next to `oww_best`) feeds one
  clip through the model frame-by-frame and returns the peak score per stem
  — `model.predict()` scores a rolling buffer one 80ms frame at a time, so
  finding how strongly a whole clip matches means walking it and resetting
  first, the same reset `_wake_loop_oww` already does between turns.
- `enroll_suggest_threshold(stem, clip_scores, current=None)` turns a few
  clips' peak scores into one number: a safety margin (`OWW_ENROLL_MARGIN`,
  0.85) below the *weakest* clip, since that's the binding constraint — a
  threshold above it means that exact utterance wouldn't have fired.
  Floored (`OWW_ENROLL_MIN_VIABLE`, 0.15) so a hopelessly weak enrollment
  returns a warning pointing at `docs/wake-word-training.md` instead of a
  number low enough to let ambient noise through. **Capped at the current
  threshold — this can only lower the bar, never raise it**: a few positive
  clips prove genuine wakes score at least this high, but say nothing about
  false positives on other audio. Raising the bar needs negative examples,
  which is direction #4, not this.
- `zeev/wake_enroll.py` — the CLI that actually drives this on the Pi:
  records N clips via `arecord` (prompting between each), scores them
  against the configured `OWW_MODEL_PATH` model(s), prints a suggestion per
  stem, and (only with `--apply`) merges it into the `.env` `OWW_THRESHOLDS`
  line — printed by default, never silently written.

This is Pi-only in practice (needs a real mic and `openwakeword`), so it
hasn't been run live yet — only the pure scoring/suggestion logic and the
`.env` merge logic are exercised here, against a scripted fake model.

Tests: `tests/test_wake_enrollment.py`.

## 4. Active learning loop from false-positive logs

False wake-word triggers (e.g. TV audio scoring 0.96) are already logged via
journalctl — see the openWakeWord section of CLAUDE.md. Auto-harvest those
as hard negatives for the next openWakeWord retraining round, closing the
loop between production failures and training data. This is the direction
the project's own docs already point at (`docs/wake-word-training.md`'s
sample-count lever) — it just isn't automated yet.
