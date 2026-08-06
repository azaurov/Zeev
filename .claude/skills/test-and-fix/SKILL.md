# Autonomous Test and Fix — Zeev

Run a structured bug-finding pass over the Zeev codebase and service, then fix everything found. Work autonomously without asking for confirmation. Deploy when done.

## Permissions required (add to `.claude/settings.json` `permissions.allow` before running)

The skill requires these entries so it can run end-to-end without approval prompts:

```json
"Bash(python3 -c *)",
"Bash(python3 << *)",
"Bash(ssh ragnar@ragnarok *)",
"Bash(journalctl *)",
"Bash(systemctl status *)",
"Bash(git -C /home/azaurov/Zeev add *)",
"Bash(git -C /home/azaurov/Zeev commit *)",
"Bash(git -C /home/azaurov/Zeev push *)",
"Bash(sudo systemctl restart zeev-device)"
```

All of the above are already present in `.claude/settings.local.json` on this machine. The `settings.json` in this repo covers `journalctl *` and `systemctl status *`.

## Scope

Target: `zeev/zeev.py` (single-file app) running as `zeev-device.service` on `ragnar@ragnarok`.

Do NOT touch: `data/`, `torah.db`, `.env`, migration scripts, or the quantum scripts unless a bug is clearly in one of them.

## Phase 1 — Static analysis (local)

Run each of these and collect all output before fixing anything:

1. **Syntax / import check**
   ```
   python3 -c "import ast, sys; ast.parse(open('zeev/zeev.py').read()); print('AST OK')"
   ```

2. **Undefined globals scan** — search for variables used before assignment inside functions, especially `global` declarations missing before use:
   ```
   grep -n "global " zeev/zeev.py | head -40
   ```
   Cross-check each `global X` against the function body to ensure `X` is declared before any conditional branch that might read it.

3. **Regex correctness** — verify all `re.compile(...)` patterns compile without error:
   ```
   python3 -c "
   import re, ast, sys
   src = open('zeev/zeev.py').read()
   tree = ast.parse(src)
   for node in ast.walk(tree):
       if isinstance(node, ast.Call):
           func = getattr(node.func, 'attr', None) or getattr(node.func, 'id', '')
           if func in ('compile', 'search', 'match', 'sub', 'findall'):
               if node.args and isinstance(node.args[0], ast.Constant):
                   try:
                       re.compile(node.args[0].value)
                   except re.error as e:
                       print(f'Line {node.lineno}: {e} — {node.args[0].value[:60]}')
   print('regex scan done')
   "
   ```

4. **requests.Response truthiness traps** — find any remaining `if resp` / `not resp` / `resp and` patterns where `resp` is a `requests.Response` object and the intent is a None-check:
   ```
   grep -n "if resp\b\|not resp\b\|resp and\b\| resp else\b" zeev/zeev.py
   ```
   Fix any that should be `resp is None` / `resp is not None`.

5. **tok_limit / max_tokens consistency** — check all `_groq_post` and `_llm_post` call sites for missing `max_tokens` overrides. The default is 400 which is too low for Torah, 70B, and R1 responses. Every call site must pass an appropriate `max_tokens`:
   ```
   grep -n "_groq_post\|_llm_post\|_openai_compat_post" zeev/zeev.py
   ```
   For each call: if the prompt can include Torah passages or the model can be 70B/R1, ensure `max_tokens >= 1200`.

6. **Timeout / idle_timeout consistency** — verify `_collect_piper_audio` is called with `idle_timeout=8.0` everywhere (not the old default):
   ```
   grep -n "_collect_piper_audio" zeev/zeev.py
   ```

7. **Thread safety** — scan for globals written outside a lock that are read from multiple threads:
   ```
   grep -n "_BT_AUDIO_DEV\|_BT_RATE\|_BT_CHANNELS\|_piper_dev_proc\|_MUSIC_PROC\|_IN_CALL\|_pregen_msg" zeev/zeev.py | head -40
   ```
   Flag any write that doesn't happen under a lock while the value is also read from another thread.

8. **Error swallowing** — find bare `except: pass` or `except Exception: pass` that silently discard errors in critical paths (TTS, STT, LLM calls):
   ```
   grep -n "except.*pass\|except:$" zeev/zeev.py | head -20
   ```

## Phase 2 — Live service check (Pi)

SSH to `ragnar@ragnarok` and run:

1. **Service health**
   ```
   journalctl -u zeev-device -n 30 --no-pager
   ```
   Look for: ERROR lines, crash loops, BrokenPipeError, failed TTS, failed STT.

2. **Piper pre-warm status**
   ```
   journalctl -u zeev-device --no-pager | grep -i "piper\|pre-warm\|tts" | tail -10
   ```

3. **Hebcal/parsha integration smoke test** — verify `get_weekly_parsha()` returns the correct upcoming parsha:
   ```
   cd /home/ragnar/Zeev && python3 -c "
   import os, sys
   sys.path.insert(0, 'zeev')
   sys.argv = ['zeev.py', '--no-greeting']
   for line in open('.env'):
       line = line.strip()
       if '=' in line and not line.startswith('#'):
           k, v = line.split('=', 1)
           os.environ[k.strip()] = v.strip()
   import zeev as z
   z.init_learning()
   p = z.get_weekly_parsha()
   print('Parsha:', p)
   t = z.get_parsha_text(p) if p else ''
   print('Text chars:', len(t))
   print('First 100:', t[:100])
   "
   ```

4. **Torah FTS smoke test** — verify a direct content search (not query-based) hits the right book:
   ```
   python3 -c "
   import sqlite3
   db = sqlite3.connect('/home/ragnar/Zeev/zeev/data/torah.db')
   rows = db.execute(\"SELECT ref, en FROM passages WHERE ref LIKE 'Numbers 16%' LIMIT 2\").fetchall()
   for ref, en in rows:
       print(ref, '-', en[:80])
   "
   ```

5. **RAG-faithfulness probe findings** — query the `rag_probes` table (populated by `zeev/rag_probe.py`) for recent UNGROUNDED rows and cross-check against `docs/rag-probe-findings.md`, which documents every finding investigated so far (probe-grading gaps already fixed: Torah/location/persona-block merges; genuine production bugs already fixed: fabricated reminders, unscoped quantum-analogy bleed, stale-conversation-as-current). Any UNGROUNDED row not already covered there is a candidate real bug or a new probe-grading gap — investigate the same way the existing entries were: check whether the answer is actually grounded in something the grader wasn't shown (another always-injected prompt block, broader history) before concluding it's a genuine fabrication.
   ```
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
   **Known pending lead (2026-08-06, not yet confirmed or fixed)**: a torah probe on "What does it mean when the Talmud says that Rabbi Ami rescinds a sale because it was a 'mistaken transaction'?" was flagged UNGROUNDED — the seed passage check passed (so `_run_torah_probe`'s reroll-on-mismatch guard didn't catch it), but the shown excerpt (Bekhorot 13b, idol-purchase/vessel-pulling rules) doesn't obviously contain "Rabbi Ami" or "mistaken transaction" in what's shown. Possible that `_gen_torah_question` is inventing terms not actually in its own seed passage's excerpt, or that the full daf (untruncated) does contain it and the excerpt is just cut short — check this first before broader static analysis.

6. **needs_* function matrix** — verify each detection function fires correctly for representative queries:
   ```
   python3 -c "
   import os, sys
   sys.path.insert(0, '/home/ragnar/Zeev/zeev')
   sys.argv = ['zeev.py', '--no-greeting']
   for line in open('/home/ragnar/Zeev/.env'):
       line = line.strip()
       if '=' in line and not line.startswith('#'):
           k, v = line.split('=', 1)
           os.environ[k.strip()] = v.strip()
   import zeev as z
   z.init_learning()
   tests = [
       (\"what's this week's Torah portion?\", z.needs_torah, True),
       (\"what's this week's Torah portion?\", z.needs_parsha_reading, True),
       (\"Torah portion\", lambda t: z.needs_torah(t) or z.needs_parsha_reading(t), True),
       (\"recite the shema\", z.needs_torah, True),
       (\"play some jazz\", z.needs_torah, False),
       (\"what's the weather\", z.needs_torah, False),
   ]
   for text, fn, expected in tests:
       result = fn(text)
       status = 'OK' if bool(result) == expected else 'FAIL'
       print(f'[{status}] {fn.__name__}({text[:40]!r}) = {result} (expected {expected})')
   "
   ```

## Phase 3 — Fix all bugs found

For each bug identified in Phase 1 and Phase 2:

1. Read the surrounding code (50 lines of context).
2. Write the minimal fix — no refactoring, no cleanup beyond the bug.
3. Apply the edit.
4. Re-run the relevant check from Phase 1/2 to confirm it passes.

Common bugs to look for and how to fix them:

- **`not resp` truthiness trap**: change to `resp is None`; change `resp else` to `resp is not None else`.
- **Missing `max_tokens`**: add `max_tokens=1200` to calls that may handle Torah/70B/R1 payloads.
- **Old `idle_timeout` value**: change any `_collect_piper_audio(p)` call without explicit `idle_timeout` to pass `idle_timeout=8.0`.
- **`needs_parsha_reading` not gating model/token overrides in web/terminal path**: ensure the `stream_reply` path also forces 70B when `needs_parsha_reading()`.
- **Bare `except: pass` in TTS/LLM path**: at minimum log the error with `print(f"[warn] {e}", flush=True)`.
- **RAG probe-grading gap** (`rag_probe.py`): a `rag_probes` UNGROUNDED finding where the answer is actually grounded in a prompt block `_history_retrieval`/`_torah_retrieval` doesn't show the grader. Check whether `_build_system_prompt` injects something unconditionally (Torah, `## Approximate location:`, `SYSTEM_PROMPT` itself — all three were missed at different points) that a new merge function needs to surface, following the existing `_torah_retrieval`/`_location_retrieval`/`_persona_context` pattern in `zeev/rag_probe.py`.

## Phase 4 — Commit and deploy

After all fixes are applied and re-checks pass:

1. Run the deploy skill: commit changes with a `fix:` prefix message listing each bug, push, SSH pull, restart `zeev-device`, tail logs to confirm clean startup.
2. Run the Phase 2 live checks again to confirm all fixes hold on the Pi.
3. Update `CLAUDE.md` to document any behaviour changes (constants, function signatures, detection logic). Do not pad — only document what changed. **RAG-faithfulness probe findings specifically go in `docs/rag-probe-findings.md`** (the full findings history now lives there, not inline in CLAUDE.md — see its own header note), with at most a one-line summary update in CLAUDE.md's "RAG-faithfulness dashboard" section if the current-state summary itself needs to change.

## Output format

After completing all phases, report:
- A numbered list of bugs found (with file:line reference).
- For each bug: what it was, what the fix was, and confirmation it passes the re-check.
- Total: N bugs found, N fixed, 0 remaining.
