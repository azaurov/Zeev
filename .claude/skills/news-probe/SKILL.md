# World-news faithfulness probe — Zeev

Run `zeev/news_probe.py` on the Pi, show the rolling faithfulness report, and
surface any new UNGROUNDED findings — the world-news equivalent of
`/rag-probe`, applied to the curated shpeel digest instead of Torah/history
RAG. This skill only runs and reports — it does not investigate or fix
anything itself.

## Scope

Read-only against production: `zeev/news_probe.py` grades each digest
`news_digest.py` already generated against the raw Tavily snippets it was
built from (stored alongside the digest, in `world_news.snippets`), via a
fresh-context grader LLM call. It never changes what a live "give me the
shpeel" turn returns. Runs on `ragnar@ragnarok`.

## Step 1 — Grade any ungraded digests

```bash
ssh ragnar@ragnarok "cd ~/Zeev && python3 zeev/news_probe.py"
```

Grades every `world_news` row that has stored snippets and hasn't been
graded yet. A digest saved before the `snippets` column existed can never be
graded (nothing to check it against) and is silently skipped — that's
expected, not a bug.

## Step 2 — Show the rolling report

```bash
ssh ragnar@ragnarok "cd ~/Zeev && python3 zeev/news_probe.py --report"
```

Prints the 30-day grounded rate plus the most recent UNGROUNDED digests with
grader notes.

## Step 3 — Read the flagged digests directly

For any UNGROUNDED result, pull the actual digest text and the snippets it
was graded against, and read them yourself before reporting to the user —
the grader is a small model and can be wrong in either direction (see
`docs/rag-probe-findings.md` for the general pattern of grading gaps this
project has already hit with the Torah/history probe):

```bash
ssh ragnar@ragnarok "cd ~/Zeev && python3 -c \"
import sys
sys.path.insert(0, 'zeev')
import news_probe
con = news_probe._open_db()
rows = con.execute('''
    SELECT news_probes.ts, news_probes.grader_note, world_news.content, world_news.snippets
    FROM news_probes JOIN world_news ON world_news.id = news_probes.digest_id
    WHERE news_probes.grounded = 0 ORDER BY news_probes.ts DESC LIMIT 5
''').fetchall()
for r in rows:
    print('---', r['ts'])
    print('NOTE:', r['grader_note'])
    print('DIGEST:', r['content'][:300])
\""
```

Judge for yourself whether the flagged claim is genuinely unsupported by the
snippets, or whether it's a reasonable summarization/inference the grader
was too strict about (the summarizer is explicitly allowed to infer and
paraphrase — see `SHPEEL_PROMPT` in `zeev/world_news.py`).

## Step 4 — Report

Tell the user:
- The rolling grounded rate from `--report`.
- Any genuinely fabricated claims (a name, number, or event not present
  anywhere in the source snippets) — this is the failure mode worth acting
  on, since a fabricated news detail spoken to Alex as fact is a real
  correctness bug, not a probe-grading nuance.
- If a flagged digest turns out to be a reasonable summarization the grader
  was too strict about, say so and don't treat it as a bug.
- If nothing new: say so plainly. A clean run is a valid, useful result.

Do not fix anything in this skill. If a digest is genuinely fabricating
details, that's a prompt or query-list problem in `zeev/world_news.py` —
hand it to `/test-and-fix` or fix it directly with the user's go-ahead, the
same as any other reported bug.
