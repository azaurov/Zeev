#!/usr/bin/env python3
"""joke_probe.py — quality/safety dashboard for Zeev's adult joke pool.

Zeev's `/joke` feature (zeev.py's random_joke()) serves from a static,
pre-curated pool (data/adult_jokes*.json, one file per language) rather than
generating jokes live -- see load_jokes(). That pool was bulk-imported from
an external corpus, so its quality and safety are unverified at the
individual-joke level; the only enforced gate is _JOKE_EXCLUDE_RE, a regex
that drops anything touching Jewish/Israeli/religious topics (this
household's own exclusion policy, not a general content filter).

This script samples real jokes from the loaded pool, grades each on four
axes via a fresh-context LLM call (the same "separate grader, no chat
history" shape rag_probe.py uses), and logs results to the `joke_probes`
table so low-quality or borderline-unsafe entries can be found and pruned
from the JSON files by hand -- this script only grades, it never edits or
removes anything from the pool itself.

Axes graded:
  funny        -- does the joke actually land, structurally (setup + turn),
                  not just "is it to your taste"
  punchline     -- is there a real punchline/twist, not a flat non-sequitur
  dirty         -- is it actually adult/explicit, not just mundane
  safe          -- does it clear this household's own exclusion policy
                  (_JOKE_EXCLUDE_RE's Jewish/Israeli/religious topics) plus
                  the baseline no responsible system serves regardless of
                  audience: no sexualized minors, no real named/identifiable
                  people, nothing that plays real non-consensual violence as
                  funny (dark absurdist bits -- animals, bar-joke surrealism
                  -- are in scope and already in the pool; content that reads
                  as endorsing actual abuse is not)

Usage:
    python3 zeev/joke_probe.py [--n N] [--lang en]   # grade N random jokes (default 15, English)
    python3 zeev/joke_probe.py --report              # rolling quality/safety report
    python3 zeev/joke_probe.py --report --report-days 7
    python3 zeev/joke_probe.py --report --show-flagged  # list jokes that failed any axis, for pruning

Not wired to a systemd timer -- unlike rag_probe.py (which watches a path
that changes on every real conversation), the joke pool only changes when
someone edits the JSON files by hand, so a scheduled re-grade of an unchanged
pool would just re-spend LLM calls for the same verdicts. Re-run manually
after editing the pool.
"""

import argparse
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import zeev  # noqa: E402  -- .env loading, GROQ_API_KEY etc. all happen at zeev's own import time

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _ensure_probes_table(zeev_mod):
    with zeev_mod._db_lock:
        zeev_mod._db().execute("""
            CREATE TABLE IF NOT EXISTS joke_probes (
                id            INTEGER PRIMARY KEY,
                ts            REAL    NOT NULL,
                lang          TEXT    NOT NULL,
                setup         TEXT    NOT NULL,
                punchline     TEXT,
                funny         INTEGER,
                has_punchline INTEGER,
                dirty         INTEGER,
                safe          INTEGER,
                grader_note   TEXT
            )
        """)
        zeev_mod._db().commit()


def _save_probe(zeev_mod, lang, setup, punchline, funny, has_punchline, dirty, safe, grader_note):
    with zeev_mod._db_lock:
        zeev_mod._db().execute(
            "INSERT INTO joke_probes (ts, lang, setup, punchline, funny, has_punchline, "
            "dirty, safe, grader_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), lang, setup, punchline, funny, has_punchline, dirty, safe, grader_note),
        )
        zeev_mod._db().commit()


def _update_probe(zeev_mod, row_id, funny, has_punchline, dirty, safe, grader_note):
    with zeev_mod._db_lock:
        zeev_mod._db().execute(
            "UPDATE joke_probes SET ts = ?, funny = ?, has_punchline = ?, dirty = ?, "
            "safe = ?, grader_note = ? WHERE id = ?",
            (time.time(), funny, has_punchline, dirty, safe, grader_note, row_id),
        )
        zeev_mod._db().commit()


def _fully_ungraded_rows(zeev_mod):
    """Rows from a previous run where every axis came back None -- the
    _grade() failure shape (rate limit, transport error, etc.), not a real
    "no" verdict on any axis. Retried in place via --retry-failed rather than
    resampled, since a fresh --n run picks new random jokes and would just
    leave these gaps unfilled."""
    with zeev_mod._db_lock:
        rows = zeev_mod._db().execute(
            "SELECT id, setup, punchline FROM joke_probes WHERE "
            "funny IS NULL AND has_punchline IS NULL AND dirty IS NULL AND safe IS NULL"
        ).fetchall()
    return rows


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def _sample_jokes(zeev_mod, lang, n, empty_punchline_only=False):
    """n random jokes from the already-filtered pool (load_jokes() has
    already dropped anything matching _JOKE_EXCLUDE_RE), without replacement
    within a single run. Grading a joke twice in one run wastes a call on a
    verdict this run already has.

    empty_punchline_only: restrict to entries whose punchline field is blank
    before sampling. Found live in the older/raw-scraped half of the pool
    (186 such entries) -- some are legitimate one-liners that never needed a
    punchline field, others look like truncated scrape artifacts (e.g. a
    setup with no resolution at all); this is the audit for telling the two
    apart rather than guessing from the setup text alone."""
    pool = zeev_mod._JOKES.get(lang) or []
    if empty_punchline_only:
        pool = [j for j in pool if not (j.get("punchline") or "").strip()]
    if not pool:
        return []
    return random.sample(pool, min(n, len(pool)))


# ---------------------------------------------------------------------------
# Grading — fresh context, no chat history
# ---------------------------------------------------------------------------
# Framed explicitly as a content-QA audit of an already-curated, opt-in adult
# joke pool for a private single-user household assistant -- not a request to
# generate new material -- so the grader model judges the four axes instead
# of declining the whole task on sight.

_GRADE_PROMPT = """You are auditing ONE joke from an already-curated adult joke pool used by a private, single-user household voice assistant. The pool is opt-in adult content for one consenting adult user; your job is quality/safety QA on existing entries, not to generate anything new.

JOKE:
Setup: {setup}
Punchline: {punchline}

Grade it on four axes. For each, answer yes or no on its own line, in this exact order:

FUNNY: does the joke actually land as a joke (a real setup + turn), regardless of taste -- not "is this to your personal preference"
PUNCHLINE: is there a genuine punchline/twist that resolves the setup, as opposed to a flat non-sequitur or the setup just trailing off
DIRTY: is it actually adult/explicit content, not just a mundane joke that happened to land in this pool
SAFE: does it clear ALL of these bars -- (a) no jokes targeting Jewish, Israeli, or other religious/ethnic identity as the target of the joke, (b) no sexualized minors under any framing, (c) no real named or identifiable living person, (d) does not present real non-consensual sexual violence as funny (dark absurdist bits involving animals or bar-joke surrealism are fine and already common in this pool -- the line is content that reads as endorsing actual abuse, not dark humor itself)

After the four yes/no lines, write one line starting "NOTE:" explaining any "no" verdict briefly. If all four are yes, NOTE can just say "clean"."""

_YES_RE = re.compile(r"\byes\b", re.I)
_NO_RE = re.compile(r"\bno\b", re.I)

_AXIS_RE = {
    "funny": re.compile(r"^FUNNY:\s*(.+)$", re.I | re.M),
    "has_punchline": re.compile(r"^PUNCHLINE:\s*(.+)$", re.I | re.M),
    "dirty": re.compile(r"^DIRTY:\s*(.+)$", re.I | re.M),
    "safe": re.compile(r"^SAFE:\s*(.+)$", re.I | re.M),
}
_NOTE_RE = re.compile(r"^NOTE:\s*(.+)$", re.I | re.M)


def _parse_verdict(line):
    """yes/no -> 1/0, None if neither word appears (unparseable)."""
    if _NO_RE.search(line):
        return 0
    if _YES_RE.search(line):
        return 1
    return None


def parse_grade(raw):
    """Raw grader text -> dict(funny, has_punchline, dirty, safe, note).
    Any axis whose line is missing or unparseable comes back None rather
    than a guessed default -- an ungraded axis should read as "unsure", not
    silently count as a pass or a fail in the report."""
    raw = (raw or "").strip()
    result = {}
    for axis, pattern in _AXIS_RE.items():
        m = pattern.search(raw)
        result[axis] = _parse_verdict(m.group(1)) if m else None
    note_m = _NOTE_RE.search(raw)
    result["note"] = note_m.group(1).strip() if note_m else (raw[:200] if raw else "empty grader response")
    return result


_RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_BACKOFF_S = (8, 20, 45)  # per-attempt sleep before the retry that follows it


def _grade(zeev_mod, setup, punchline):
    """Grades one joke, retrying on Groq's 429 ("rate-limited" -- see
    _llm_complete's groq branch) with increasing backoff. A 40-joke run at
    ~1 grading call/sec routinely outruns Groq's free-tier per-minute limit
    partway through (observed live: 11/40 calls failed this way in one run),
    and those calls are cheap/short (max_tokens=150) so a short wait clears
    it rather than needing the request rerouted elsewhere."""
    msgs = [{"role": "user", "content": _GRADE_PROMPT.format(setup=setup, punchline=punchline or "(none)")}]
    err = None
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        text, err = zeev_mod._llm_complete(msgs, zeev_mod.MODELS["2"][0], max_tokens=150)
        if err != "rate-limited":
            break
        if attempt < _RATE_LIMIT_RETRIES:
            time.sleep(_RATE_LIMIT_BACKOFF_S[attempt])
    if err or not text:
        return dict(funny=None, has_punchline=None, dirty=None, safe=None,
                     note=f"grader call failed: {err}")
    return parse_grade(text)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_probes(zeev_mod, n=15, lang="en", verbose=True, empty_punchline_only=False):
    _ensure_probes_table(zeev_mod)
    jokes = _sample_jokes(zeev_mod, lang, n, empty_punchline_only=empty_punchline_only)
    if not jokes:
        print(f"No jokes loaded for lang={lang!r} -- is data/adult_jokes*.json present?")
        return 0, 0

    saved = skipped = 0
    for i, j in enumerate(jokes):
        setup, punchline = j.get("setup", ""), j.get("punchline", "")
        if verbose:
            print(f"[{i + 1}/{len(jokes)}] {setup[:70]!r}...", flush=True)
        try:
            g = _grade(zeev_mod, setup, punchline)
        except Exception as e:
            print(f"  probe crashed: {e}")
            skipped += 1
            continue
        _save_probe(zeev_mod, lang, setup, punchline, g["funny"], g["has_punchline"],
                    g["dirty"], g["safe"], g["note"])
        saved += 1
        if verbose:
            def label(v):
                return {1: "yes", 0: "NO", None: "?"}[v]
            print(f"  funny={label(g['funny'])} punchline={label(g['has_punchline'])} "
                  f"dirty={label(g['dirty'])} safe={label(g['safe'])}  {g['note']}")
    print(f"\nDone: {saved} joke(s) graded, {skipped} skipped.")
    return saved, skipped


def retry_failed(zeev_mod, verbose=True):
    """Re-grade every fully-ungraded row in place (see _fully_ungraded_rows).
    _grade() itself now retries a live 429 with backoff, so this is for the
    rarer case of a run that still exhausted those retries, or an older run
    from before that retry logic existed."""
    _ensure_probes_table(zeev_mod)
    rows = _fully_ungraded_rows(zeev_mod)
    if not rows:
        print("No ungraded rows to retry.")
        return 0, 0

    fixed = still_failed = 0
    for i, row in enumerate(rows):
        if verbose:
            print(f"[{i + 1}/{len(rows)}] retrying {row['setup'][:70]!r}...", flush=True)
        try:
            g = _grade(zeev_mod, row["setup"], row["punchline"])
        except Exception as e:
            print(f"  probe crashed: {e}")
            still_failed += 1
            continue
        _update_probe(zeev_mod, row["id"], g["funny"], g["has_punchline"], g["dirty"], g["safe"], g["note"])
        if g["funny"] is None and g["has_punchline"] is None and g["dirty"] is None and g["safe"] is None:
            still_failed += 1
            if verbose:
                print(f"  still failing: {g['note']}")
        else:
            fixed += 1
            if verbose:
                def label(v):
                    return {1: "yes", 0: "NO", None: "?"}[v]
                print(f"  funny={label(g['funny'])} punchline={label(g['has_punchline'])} "
                      f"dirty={label(g['dirty'])} safe={label(g['safe'])}  {g['note']}")
    print(f"\nDone: {fixed} row(s) filled in, {still_failed} still failing.")
    return fixed, still_failed


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _rate(rows, axis):
    """rows: list of sqlite3.Row-like dicts. Returns (pass, judged, total)."""
    total = len(rows)
    judged = sum(1 for r in rows if r[axis] is not None)
    passed = sum(1 for r in rows if r[axis] == 1)
    return passed, judged, total


def report(zeev_mod, days=30, lang=None, show_flagged=False):
    cutoff = time.time() - days * 86400
    query = "SELECT * FROM joke_probes WHERE ts >= ?"
    params = [cutoff]
    if lang:
        query += " AND lang = ?"
        params.append(lang)

    with zeev_mod._db_lock:
        rows = zeev_mod._db().execute(query, params).fetchall()

    print(f"Joke pool quality/safety — last {days} days" + (f" (lang={lang})" if lang else ""))
    print("=" * 60)
    if not rows:
        print("No probes recorded yet.")
        return

    print(f"  {len(rows)} joke(s) graded")
    for axis, label in (("funny", "funny"), ("has_punchline", "good punchline"),
                        ("dirty", "sufficiently dirty"), ("safe", "safe")):
        passed, judged, total = _rate(rows, axis)
        rate = (passed / judged * 100) if judged else float("nan")
        print(f"  {label:20s} {passed}/{judged} ({rate:.0f}%)"
              + (f"   [{total - judged} unparseable]" if judged < total else ""))

    if show_flagged:
        flagged = [r for r in rows if any(r[a] == 0 for a in ("funny", "has_punchline", "dirty", "safe"))]
        print(f"\nFlagged ({len(flagged)}) — candidates for pruning from the pool:")
        if not flagged:
            print("  (none)")
        for r in flagged:
            when = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M")
            fails = [a for a in ("funny", "has_punchline", "dirty", "safe") if r[a] == 0]
            print(f"  [{when}] fails={','.join(fails)}: {r['setup'][:80]!r}")
            print(f"      {r['grader_note']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Zeev adult-joke-pool quality/safety probe")
    parser.add_argument("--n", type=int, default=15,
                         help="Number of jokes to sample and grade (default 15)")
    parser.add_argument("--lang", default=None, choices=["en", "es", "ru", "he", "zh"],
                         help="Joke pool language. Grading defaults to en; --report with no "
                              "--lang shows all languages combined")
    parser.add_argument("--report", action="store_true",
                         help="Print the rolling quality/safety report and exit (no grading run)")
    parser.add_argument("--report-days", type=int, default=30,
                         help="Rolling window in days for --report (default 30)")
    parser.add_argument("--show-flagged", action="store_true",
                         help="With --report, list individual jokes that failed any axis")
    parser.add_argument("--retry-failed", action="store_true",
                         help="Re-grade rows that came back fully ungraded (rate limit, "
                              "transport error, etc.) instead of sampling new jokes")
    parser.add_argument("--empty-punchline-only", action="store_true",
                         help="Sample only from entries with a blank punchline field -- "
                              "tells apart legitimate one-liners from truncated scrape junk")
    args = parser.parse_args()

    zeev.load_jokes()

    if args.report:
        report(zeev, days=args.report_days, lang=args.lang, show_flagged=args.show_flagged)
        return

    if not zeev.GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    if args.retry_failed:
        fixed, still_failed = retry_failed(zeev)
        sys.exit(0 if fixed > 0 or still_failed == 0 else 1)

    lang = args.lang or "en"
    print(f"Joke pool probe — {args.n} joke(s), lang={lang}"
          + (" (empty-punchline only)" if args.empty_punchline_only else ""))
    saved, skipped = run_probes(zeev, n=args.n, lang=lang, empty_punchline_only=args.empty_punchline_only)
    sys.exit(0 if saved > 0 or skipped == 0 else 1)


if __name__ == "__main__":
    main()
