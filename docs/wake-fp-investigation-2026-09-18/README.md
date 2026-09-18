# Wake-word false-positive investigation, 2026-09-18

Four hypotheses for `hey_zeev` firing ~1.4x/hour on non-wake audio, each tested by a separate
agent in its own git worktree. **None was established as the cause.** The lasting result is
methodological: downloaded broadcast/news/podcast speech cannot reproduce this fault at all
(42 min / 31,498 frames per model peaked at 0.0426 / 0.0155 against live thresholds of
0.84 / 0.95), so only audio captured *through the real mic and enclosure* can.

| | Hypothesis | Verdict |
|---|---|---|
| H1 | The model can't separate the phrase from ordinary speech | REFUTED (broadcast speech only) |
| H2 | The energy gate's reset+preroll stitching manufactures triggers | Mechanism CONFIRMED (+0.0003 max, frames 0-5 after a reset); cause INCONCLUSIVE |
| H3 | Zeev wakes itself on its own TTS | REFUTED (TTS peaked 0.0066; 14 real triggers, none inside the 1.5s settle window) |
| H4 | FPs are single-frame spikes, genuine wakes sustained | INCONCLUSIVE (no crossings to build a histogram from) |

Read the per-hypothesis files for numbers, harness details and the protocols to run against real
captures (`wake_capture_save()` writes them to `zeev/data/wake_captures/` on the Pi, live since
2026-09-18 09:18). The harness scripts and the H4 capture analyzer (with 7 passing tests) were
left in the agent worktrees under `.claude/worktrees/` — git-excluded, so copy them out before
cleaning those up.
