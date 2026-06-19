# Autonomous Test and Fix — Zeev

Run a structured bug-finding pass over the Zeev codebase and service, then fix everything found. Work autonomously without asking for confirmation. Deploy when done.

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

5. **needs_* function matrix** — verify each detection function fires correctly for representative queries:
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
       (\"what's this week's Torah portion?\", lambda t: z.route_model(t) == z.MODELS['2'][0], False),
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

## Phase 4 — Commit and deploy

After all fixes are applied and re-checks pass:

1. Run the deploy skill: commit changes with a `fix:` prefix message listing each bug, push, SSH pull, restart `zeev-device`, tail logs to confirm clean startup.
2. Run the Phase 2 live checks again to confirm all fixes hold on the Pi.
3. Update `CLAUDE.md` to document any behaviour changes (constants, function signatures, detection logic). Do not pad — only document what changed.

## Output format

After completing all phases, report:
- A numbered list of bugs found (with file:line reference).
- For each bug: what it was, what the fix was, and confirmation it passes the re-check.
- Total: N bugs found, N fixed, 0 remaining.
