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

## 2. Cross-modal grounding via the LLM as the shared space

Rather than training a joint audio/vision encoder, use natural language as
the alignment layer that already exists: camera frames are described in text
via `vision_complete()`, and that text is already retrievable by the same
semantic-search path as everything else. This gets a rough multimodal
synergy for free — the alignment work is outsourced to a pretrained LLM
instead of trained from scratch.

## 3. Few-shot wake-word personalization

Instead of contrastive pretraining, a small per-household enrollment step:
record a handful of samples of Alex saying the wake phrase, then adjust
either the existing classifier's threshold or a lightweight per-user
embedding distance. A much smaller ML problem than full retraining, and
plausibly Pi-feasible where a from-scratch contrastive/CLIP model is not.

## 4. Active learning loop from false-positive logs

False wake-word triggers (e.g. TV audio scoring 0.96) are already logged via
journalctl — see the openWakeWord section of CLAUDE.md. Auto-harvest those
as hard negatives for the next openWakeWord retraining round, closing the
loop between production failures and training data. This is the direction
the project's own docs already point at (`docs/wake-word-training.md`'s
sample-count lever) — it just isn't automated yet.
