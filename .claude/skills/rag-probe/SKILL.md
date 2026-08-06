# RAG-faithfulness probe — Zeev

Run a fresh batch of `zeev/rag_probe.py` on the Pi, show the rolling faithfulness
report, and surface any new UNGROUNDED findings not already covered in
`docs/rag-probe-findings.md`. This skill only runs and reports — it does not
investigate or fix anything itself. Hand genuinely new findings to
`/test-and-fix`, which has its own dedicated phase for exactly this.

## Scope

Read-only against production: `zeev/rag_probe.py` runs real questions through
the real answering path and grades them, but never changes any live
request-handling behavior. Runs on `ragnar@ragnarok`.

## Step 1 — Run a fresh batch

```bash
ssh ragnar@ragnarok "cd ~/Zeev && python3 zeev/rag_probe.py"
```

Default is 8 probes (alternating torah/history). This can take a few minutes
and commonly exceeds a single tool call's timeout — if so, let it run in the
background and continue once it completes rather than re-running it.

## Step 2 — Show the rolling report

```bash
ssh ragnar@ragnarok "cd ~/Zeev && python3 zeev/rag_probe.py --report"
```

Prints the 30-day grounded rate by source (torah/history) plus the most
recent UNGROUNDED rows with grader notes.

## Step 3 — Cross-check against documented findings

Query the rows from the batch just run and compare against
`docs/rag-probe-findings.md`, which documents every finding investigated so
far (both fixed production bugs and fixed/accepted probe-grading gaps):

```bash
ssh ragnar@ragnarok "cd ~/Zeev && python3 -c \"
import sys
sys.path.insert(0, 'zeev')
import zeev
con = zeev._db()
rows = con.execute('SELECT ts, source, question, grader_note FROM rag_probes WHERE grounded=0 ORDER BY ts DESC LIMIT 15').fetchall()
for r in rows:
    print(r['ts'], r['source'], repr(r['question'])[:80])
    print('  ', r['grader_note'][:150])
\""
```

Read `docs/rag-probe-findings.md` and check each UNGROUNDED row against it.
Most repeat findings will already be covered (the same handful of themes —
Uncle Sasha, quantum-analogy bleed, persona naming — recur because they draw
from real conversation history that keeps getting resampled). Only flag rows
that are **genuinely new** — a different root cause, not a new instance of an
already-documented pattern.

## Step 4 — Report

Tell the user:
- The rolling grounded rate from `--report`.
- Any genuinely new UNGROUNDED findings (question, answer, grader note, and
  your initial read on whether it looks like a probe-grading gap or a real
  production bug — same judgment call every existing entry in
  `docs/rag-probe-findings.md` had to make).
- If nothing new: say so plainly. A clean run is a valid, useful result, not
  a failure to find something.

Do not fix anything in this skill. If something genuinely new and actionable
turns up, suggest running `/test-and-fix` next — that skill's Phase 2 step 5
does the actual investigation (checking whether the answer is grounded in a
prompt block the grader wasn't shown, before concluding it's a real bug) and
Phase 3/4 do the fix and deploy.
