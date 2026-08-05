#!/usr/bin/env python3
"""rag_probe.py — RAG-faithfulness dashboard for Zeev.

Runs the two retrieval-augmented paths Zeev has (Torah RAG over data/torah.db,
and history RAG over data/zeev.db's message log) through the REAL production
answering path, then grades each answer against exactly what was retrieved,
via a separate fresh-context LLM call. Results land in the `rag_probes` table
so drift/hallucination can be tracked over time, the same way
quantum_daily.py and weekly_reflection.py log their own runs.

This imports zeev.py as a module to reuse its retrieval and system-prompt
code verbatim (torah_search, retrieve_semantic/retrieve_relevant,
_build_system_prompt) rather than re-implementing it -- the whole point is
grading what production actually does, not a synthetic approximation of it.
One documented gap: production streams the reply sentence-by-sentence via
_llm_post/_groq_post_with_fallback so device mode can start speaking before
the reply finishes; this script uses the non-streaming _llm_complete() helper
that already exists in zeev.py (same endpoint, same model, same params,
stream=False) since a standalone probe has no listener to stream to -- the
text that lands in the DB is byte-for-byte the same either way.

Usage:
    python3 zeev/rag_probe.py [--n N]     # run N probes (default 8, alternating torah/history)
    python3 zeev/rag_probe.py --report               # rolling 30-day faithfulness report
    python3 zeev/rag_probe.py --report --report-days 7

Scheduled on the Pi via systemd (zeev-rag-probe.service + .timer), the same
mechanism quantum_daily.py/weekly_reflection.py use rather than literal cron --
a Pi asleep at the scheduled time loses a plain cron firing outright, while a
systemd timer with Persistent=true catches up on next boot. Staggered 06:30,
between quantum_daily's 06:00 and weekly_reflection's Sunday 07:00.
"""

import argparse
import re
import sqlite3
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
# Reuses zeev's own _db()/_db_lock rather than opening a second sqlite3
# connection to the same WAL file -- this script already needs the zeev
# module loaded for retrieval/prompt code, so there is one connection, not two.

def _ensure_probes_table(zeev_mod):
    with zeev_mod._db_lock:
        zeev_mod._db().execute("""
            CREATE TABLE IF NOT EXISTS rag_probes (
                id             INTEGER PRIMARY KEY,
                ts             REAL    NOT NULL,
                source         TEXT    NOT NULL,
                question       TEXT    NOT NULL,
                retrieved_ref  TEXT,
                retrieved_text TEXT,
                answer         TEXT,
                grounded       INTEGER,
                grader_note    TEXT
            )
        """)
        zeev_mod._db().commit()


def _save_probe(zeev_mod, source, question, retrieved_ref, retrieved_text,
                 answer, grounded, grader_note):
    with zeev_mod._db_lock:
        zeev_mod._db().execute(
            "INSERT INTO rag_probes (ts, source, question, retrieved_ref, "
            "retrieved_text, answer, grounded, grader_note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), source, question, retrieved_ref, retrieved_text,
             answer, grounded, grader_note),
        )
        zeev_mod._db().commit()


# ---------------------------------------------------------------------------
# Question generation
# ---------------------------------------------------------------------------

_TORAH_QUESTION_PROMPT = """Below is a passage from a religious text (Torah, Talmud, Siddur, etc). Write ONE short, natural question a real person might casually ask a voice assistant about its content or meaning -- the way someone actually talks, not the way a scholar cites a source.

Do NOT name the book, chapter, verse number, or the passage's own title/reference.
Do NOT quote the passage verbatim.
Output only the question itself, nothing else.

PASSAGE:
{excerpt}"""


def _gen_torah_question(zeev_mod, en_text):
    """Ask a cheap model to write a question about `en_text` without naming
    its source -- the prompt is never given the ref at all, so it can't leak
    what it was never told."""
    excerpt = (en_text or "")[:2000]
    msgs = [{"role": "user", "content": _TORAH_QUESTION_PROMPT.format(excerpt=excerpt)}]
    text, err = zeev_mod._llm_complete(msgs, zeev_mod.MODELS["1"][0], max_tokens=80)
    if err or not text:
        return None, err
    return text.strip().strip('"'), None


def _looks_like_real_question(zeev_mod, text):
    """Same shape as the wake-word follow-up noise guard (zeev.py's
    re.search(r'\\w{2,}') check, tightened to \\w{3,} here) plus the shared
    Whisper-hallucination filter -- both exist because the messages table can
    contain STT noise/junk, and a probe question sampled from junk grades
    nothing meaningful."""
    text = (text or "").strip()
    if len(text) < 15:
        return False
    if zeev_mod._is_whisper_hallucination(text):
        return False
    if not re.search(r"\w{3,}", text):
        return False
    return True


def _random_torah_passage(zeev_mod):
    """One (ref, en) row from torah.db's passages table, or None if the DB is
    missing/empty/unreadable. Direct read-only connection, mirroring how
    torah_search() itself opens TORAH_DB -- but ORDER BY RANDOM() over the
    whole table, which torah_search never does, so this is its own query
    rather than a call into it."""
    if not zeev_mod.TORAH_DB.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{zeev_mod.TORAH_DB}?mode=ro", uri=True)
        row = con.execute(
            "SELECT ref, en FROM passages WHERE en IS NOT NULL AND length(en) > 200 "
            "ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
        con.close()
        return row
    except sqlite3.Error:
        return None


def _random_history_message(zeev_mod, max_attempts=8):
    """A real past user-role message from `messages`, noise-filtered. Rerolls
    in-process up to max_attempts rather than bubbling a single reject up to
    the caller, since most rows are ordinary and a reroll is cheap."""
    for _ in range(max_attempts):
        with zeev_mod._db_lock:
            row = zeev_mod._db().execute(
                "SELECT id, content FROM messages WHERE role = 'user' "
                "ORDER BY RANDOM() LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        text = (row["content"] or "").strip()
        if _looks_like_real_question(zeev_mod, text):
            return row["id"], text
    return None


# ---------------------------------------------------------------------------
# Answering — the real production path, non-streaming
# ---------------------------------------------------------------------------

def _answer_question(zeev_mod, question):
    """Mirrors the model/token-limit selection at the web /chat handler
    (run_web_server, zeev.py ~line 9800-9899): route_model picks a model,
    then Torah/parsha content forces the 70B plus a higher cap because it
    would exceed the 8B's 6k TPM limit. needs_search/Tavily is deliberately
    left out -- it's a third RAG-ish path this dashboard doesn't cover."""
    model = zeev_mod.route_model(question)
    if zeev_mod.needs_parsha_reading(question) or zeev_mod.needs_torah(question):
        model = zeev_mod.MODELS["2"][0]

    if zeev_mod.needs_parsha_reading(question):
        tok_limit = 1600
    elif zeev_mod.needs_torah(question):
        tok_limit = 1200
    elif model in (zeev_mod.MODELS["3"][0], zeev_mod.MODELS["2"][0]):
        tok_limit = 1200
    else:
        tok_limit = 600

    sys_prompt = zeev_mod._build_system_prompt(question)
    msgs = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": question}]
    answer, err = zeev_mod._llm_complete(msgs, model, max_tokens=tok_limit)
    return sys_prompt, answer, err


# Every section _build_system_prompt appends starts with a literal "\n\n",
# and the retrieved blocks themselves are always single-newline-joined (torah
# hits by "\n", history pairs by "\n---\n") -- so the next real double
# newline is always the start of the NEXT section, whatever it's headed with
# ("## ...", the bare trailing language instruction, "[Web search results
# ...]"). Anchoring on "## <label>:" would miss the several trailing sections
# that aren't "##"-prefixed; this anchors on the one thing every append does.
_TORAH_BLOCK_RE = re.compile(
    r"## Relevant Torah/Talmud passages:\n(.*?)(?=\n\n|\Z)", re.DOTALL)
_HISTORY_BLOCK_RE = re.compile(
    r"## Relevant past exchanges:\n(.*?)(?=\n\n|\Z)", re.DOTALL)


def _torah_retrieval(zeev_mod, question, sys_prompt):
    m = _TORAH_BLOCK_RE.search(sys_prompt)
    if not m:
        return "", ""
    hits = zeev_mod.torah_search(question)
    ref = ", ".join(r for r, _en, _he in hits)
    return ref, m.group(1).strip()


def _history_retrieval(zeev_mod, question, sys_prompt):
    m = _HISTORY_BLOCK_RE.search(sys_prompt)
    if not m:
        return "", ""
    hits = zeev_mod.retrieve_semantic(question) or zeev_mod.retrieve_relevant(question)
    ids = []
    if hits:
        with zeev_mod._db_lock:
            con = zeev_mod._db()
            for u, _a in hits:
                row = con.execute(
                    "SELECT id FROM messages WHERE role = 'user' AND content = ? "
                    "ORDER BY id DESC LIMIT 1", (u,),
                ).fetchone()
                if row:
                    ids.append(str(row["id"]))
    ref, text = ", ".join(ids), m.group(1).strip()

    # A question that lands a history hit can ALSO trigger needs_torah() --
    # the two gates are independent -- so the real system prompt can carry
    # both blocks at once. Found live 2026-08-05: "help with the bedtime angel
    # prayer" correctly answered from the injected Torah/Siddur passage, but
    # this function only ever extracted the history block, so the grader saw
    # an irrelevant slice of context and flagged a well-grounded answer as
    # UNGROUNDED. Merge in any concurrently-triggered Torah block so the
    # grader sees everything the answer could legitimately have drawn from.
    torah_ref, torah_text = _torah_retrieval(zeev_mod, question, sys_prompt)
    if torah_text:
        ref = ", ".join(p for p in (ref, torah_ref) if p)
        text = "\n\n".join(p for p in (text, torah_text) if p)

    return ref, text


# ---------------------------------------------------------------------------
# Grading — fresh context, no chat history
# ---------------------------------------------------------------------------

_GRADE_PROMPT = """Below is a passage of retrieved context and an answer that was generated using it.

CONTEXT:
{context}

ANSWER:
{answer}

Does this answer state anything not supported by the passage/context above? Reply GROUNDED, UNGROUNDED, or UNSURE on the first line, then one line explaining why."""

_UNGROUNDED_RE = re.compile(r"\bUNGROUNDED\b", re.I)
_GROUNDED_RE   = re.compile(r"\bGROUNDED\b", re.I)
_UNSURE_RE     = re.compile(r"\bUNSURE\b", re.I)


def parse_grade(raw):
    """First line -> (grounded: 1|0|None, note: str).

    UNGROUNDED is checked before GROUNDED even though a naive containment
    check ("GROUNDED" in text) would appear to work -- "GROUNDED" is a
    literal substring of "UNGROUNDED" starting at index 2, so order matters
    the moment the check stops being a whole-word regex. It already is one
    here (\\b...\\b), but the check order is kept UNGROUNDED-first anyway so
    a future loosening of the pattern doesn't silently flip the verdict.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, "empty grader response"
    first_line, _, rest = raw.partition("\n")
    note = rest.strip()

    for pattern, value in ((_UNGROUNDED_RE, 0), (_UNSURE_RE, None), (_GROUNDED_RE, 1)):
        if pattern.search(first_line):
            return value, note

    # Model didn't put the verdict on its own first line -- scan the whole
    # reply before giving up, still UNGROUNDED-first.
    for pattern, value in ((_UNGROUNDED_RE, 0), (_UNSURE_RE, None), (_GROUNDED_RE, 1)):
        if pattern.search(raw):
            return value, note or raw[:200]

    return None, f"unparseable grader output: {raw[:200]}"


def _grade(zeev_mod, context, answer):
    msgs = [{"role": "user", "content": _GRADE_PROMPT.format(context=context, answer=answer)}]
    text, err = zeev_mod._llm_complete(msgs, zeev_mod.MODELS["2"][0], max_tokens=150)
    if err or not text:
        return None, f"grader call failed: {err}"
    return parse_grade(text)


# ---------------------------------------------------------------------------
# Per-source probes
# ---------------------------------------------------------------------------

def _run_torah_probe(zeev_mod, verbose=True, max_attempts=3):
    for _ in range(max_attempts):
        row = _random_torah_passage(zeev_mod)
        if row is None:
            if verbose:
                print("  [torah] no usable passages in torah.db -- skipping")
            return None
        ref, en = row
        question, err = _gen_torah_question(zeev_mod, en)
        if err or not question:
            if verbose:
                print(f"  [torah] question generation failed ({err}) -- retrying")
            continue
        if zeev_mod.needs_parsha_reading(question):
            # Different mechanism entirely (whole-chapter parsha text via
            # get_weekly_parsha/get_parsha_text, not torah_search) -- out of
            # scope for this probe. Reroll.
            continue

        sys_prompt, answer, aerr = _answer_question(zeev_mod, question)
        if aerr or not answer:
            if verbose:
                print(f"  [torah] answer generation failed ({aerr}) -- retrying")
            continue

        retrieved_ref, retrieved_text = _torah_retrieval(zeev_mod, question, sys_prompt)
        if not retrieved_text:
            if verbose:
                print(f"  [torah] {question!r} triggered no retrieval -- rerolling")
            continue

        grounded, note = _grade(zeev_mod, retrieved_text, answer)
        if verbose:
            verdict = {1: "GROUNDED", 0: "UNGROUNDED", None: "UNSURE"}[grounded]
            print(f"  [torah] {question!r} -> {verdict}")
        return dict(source="torah", question=question, retrieved_ref=retrieved_ref,
                    retrieved_text=retrieved_text, answer=answer,
                    grounded=grounded, grader_note=note)
    return None


def _run_history_probe(zeev_mod, verbose=True, max_attempts=5):
    for _ in range(max_attempts):
        sampled = _random_history_message(zeev_mod)
        if sampled is None:
            if verbose:
                print("  [history] no usable messages in zeev.db -- skipping")
            return None
        _mid, question = sampled

        sys_prompt, answer, aerr = _answer_question(zeev_mod, question)
        if aerr or not answer:
            if verbose:
                print(f"  [history] answer generation failed ({aerr}) -- retrying")
            continue

        retrieved_ref, retrieved_text = _history_retrieval(zeev_mod, question, sys_prompt)
        if not retrieved_text:
            if verbose:
                print(f"  [history] {question!r} triggered no retrieval -- rerolling")
            continue

        grounded, note = _grade(zeev_mod, retrieved_text, answer)
        if verbose:
            verdict = {1: "GROUNDED", 0: "UNGROUNDED", None: "UNSURE"}[grounded]
            print(f"  [history] {question!r} -> {verdict}")
        return dict(source="history", question=question, retrieved_ref=retrieved_ref,
                    retrieved_text=retrieved_text, answer=answer,
                    grounded=grounded, grader_note=note)
    return None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_probes(zeev_mod, n=8, verbose=True):
    _ensure_probes_table(zeev_mod)
    saved = skipped = 0
    for i in range(n):
        source = "torah" if i % 2 == 0 else "history"
        if verbose:
            print(f"[{i + 1}/{n}] {source} probe...", flush=True)
        try:
            result = (_run_torah_probe(zeev_mod, verbose) if source == "torah"
                       else _run_history_probe(zeev_mod, verbose))
        except Exception as e:
            print(f"  [{source}] probe crashed: {e}")
            result = None
        if result is None:
            skipped += 1
            continue
        _save_probe(zeev_mod, **result)
        saved += 1
    print(f"\nDone: {saved} probe(s) saved, {skipped} skipped.")
    return saved, skipped


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _grounded_rate_by_source(rows):
    """rows: iterable of (source, grounded). Pure so it's testable without a DB."""
    by_source = {}
    for source, grounded in rows:
        d = by_source.setdefault(source, {"total": 0, "grounded": 0, "ungrounded": 0, "unsure": 0})
        d["total"] += 1
        if grounded == 1:
            d["grounded"] += 1
        elif grounded == 0:
            d["ungrounded"] += 1
        else:
            d["unsure"] += 1
    return by_source


def report(zeev_mod, days=30):
    cutoff = time.time() - days * 86400
    with zeev_mod._db_lock:
        con = zeev_mod._db()
        rows = con.execute(
            "SELECT source, grounded FROM rag_probes WHERE ts >= ?", (cutoff,)
        ).fetchall()
        ungrounded = con.execute(
            "SELECT ts, source, question, grader_note FROM rag_probes "
            "WHERE grounded = 0 AND ts >= ? ORDER BY ts DESC LIMIT 10", (cutoff,)
        ).fetchall()

    by_source = _grounded_rate_by_source((r["source"], r["grounded"]) for r in rows)

    print(f"RAG faithfulness — last {days} days")
    print("=" * 60)
    if not by_source:
        print("No probes recorded yet.")
    for source in sorted(by_source):
        d = by_source[source]
        judged = d["grounded"] + d["ungrounded"]
        rate = (d["grounded"] / judged * 100) if judged else float("nan")
        print(f"  {source:10s} {d['total']:4d} probes   "
              f"grounded {d['grounded']}/{judged} ({rate:.0f}%)   unsure {d['unsure']}")

    print()
    print(f"Most recent UNGROUNDED ({len(ungrounded)}) — candidate real bugs:")
    if not ungrounded:
        print("  (none)")
    for r in ungrounded:
        when = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M")
        print(f"  [{when}] {r['source']}: {r['question'][:80]}")
        print(f"      {r['grader_note']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Zeev RAG-faithfulness probe")
    parser.add_argument("--n", type=int, default=8,
                         help="Number of probes to run, alternating torah/history (default 8)")
    parser.add_argument("--report", action="store_true",
                         help="Print the faithfulness report and exit (no probes run)")
    parser.add_argument("--report-days", type=int, default=30,
                         help="Rolling window in days for --report (default 30)")
    args = parser.parse_args()

    if args.report:
        report(zeev, days=args.report_days)
        return

    if not zeev.GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    # Minimal subset of zeev.init_learning() needed for _build_system_prompt()
    # to produce a faithful prompt (facts/notes/reflection/RAG index), without
    # the background threads (battery poll, memory maintenance) init_learning()
    # also starts -- this is a one-shot script, not a long-running daemon.
    zeev.USER_FACTS = zeev.load_memory()
    zeev.USER_NOTES = zeev.load_notes()
    zeev.load_settings()
    zeev.load_latest_reflection()
    zeev.build_rag_index()

    print(f"RAG faithfulness probe — {args.n} probes")
    saved, skipped = run_probes(zeev, n=args.n)
    sys.exit(0 if saved > 0 or skipped == 0 else 1)


if __name__ == "__main__":
    main()
