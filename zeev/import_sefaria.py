#!/usr/bin/env python3
"""
import_sefaria.py — Download Tanakh, Mishna, and Gemara from Sefaria's public API
into a local SQLite FTS5 database at zeev/data/torah.db.

Usage:
    python3 zeev/import_sefaria.py [--corpus tanakh,mishna,gemara]

Options:
    --corpus    Comma-separated list of corpora to import (default: tanakh,mishna,gemara)
                Use 'tanakh,mishna' to skip Gemara (saves ~200MB and ~2hrs download time).

Resume-safe: already-imported refs are skipped. Re-run freely after interruption.

Estimated download time (3 parallel workers):
    Tanakh:  ~929 chapters  → ~5 min
    Mishna:  ~525 chapters  → ~3 min
    Gemara: ~7400 daf-sides → ~45 min
    Total:                  → ~53 min (Tanakh+Mishna only: ~8 min)

Estimated DB size:
    Tanakh+Mishna: ~30 MB
    + Gemara:      ~250 MB
"""

import json
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock, Semaphore

DB_PATH  = Path(__file__).resolve().parent / "data" / "torah.db"
API_BASE = "https://www.sefaria.org/api/texts"

# ── Text corpus definitions ─────────────────────────────────────────────────

TANAKH = [
    # Torah
    ("Genesis", 50), ("Exodus", 40), ("Leviticus", 27),
    ("Numbers", 36), ("Deuteronomy", 34),
    # Nevi'im
    ("Joshua", 24), ("Judges", 21), ("I Samuel", 31), ("II Samuel", 24),
    ("I Kings", 22), ("II Kings", 25), ("Isaiah", 66), ("Jeremiah", 52),
    ("Ezekiel", 48), ("Hosea", 14), ("Joel", 4), ("Amos", 9),
    ("Obadiah", 1), ("Jonah", 4), ("Micah", 7), ("Nahum", 3),
    ("Habakkuk", 3), ("Zephaniah", 3), ("Haggai", 2),
    ("Zechariah", 14), ("Malachi", 3),
    # Ketuvim
    ("Psalms", 150), ("Proverbs", 31), ("Job", 42), ("Song of Songs", 8),
    ("Ruth", 4), ("Lamentations", 5), ("Ecclesiastes", 12), ("Esther", 10),
    ("Daniel", 12), ("Ezra", 10), ("Nehemiah", 13),
    ("I Chronicles", 29), ("II Chronicles", 36),
]

MISHNA = [
    # Zeraim
    ("Berakhot", 9), ("Peah", 8), ("Demai", 7), ("Kilayim", 9),
    ("Sheviit", 10), ("Terumot", 11), ("Maasrot", 5), ("Maaser Sheni", 5),
    ("Challah", 4), ("Orlah", 3), ("Bikkurim", 4),
    # Moed
    ("Shabbat", 24), ("Eruvin", 10), ("Pesachim", 10), ("Shekalim", 8),
    ("Yoma", 8), ("Sukkah", 5), ("Beitzah", 5), ("Rosh Hashanah", 4),
    ("Taanit", 4), ("Megillah", 4), ("Moed Katan", 3), ("Chagigah", 3),
    # Nashim
    ("Yevamot", 16), ("Ketubot", 13), ("Nedarim", 11), ("Nazir", 9),
    ("Sotah", 9), ("Gittin", 9), ("Kiddushin", 4),
    # Nezikin
    ("Bava Kamma", 10), ("Bava Metzia", 10), ("Bava Batra", 10),
    ("Sanhedrin", 11), ("Makkot", 3), ("Shevuot", 8), ("Eduyot", 8),
    ("Avodah Zarah", 5), ("Avot", 6), ("Horayot", 3),
    # Kodashim
    ("Zevachim", 14), ("Menachot", 13), ("Chullin", 12), ("Bekhorot", 9),
    ("Arakhin", 9), ("Temurah", 7), ("Keritot", 6), ("Meilah", 6),
    ("Tamid", 7), ("Middot", 5), ("Kinnim", 3),
    # Taharot
    ("Keilim", 30), ("Oholot", 18), ("Negaim", 14), ("Parah", 12),
    ("Tahorot", 10), ("Mikvaot", 10), ("Niddah", 10), ("Makhshirin", 6),
    ("Zavim", 5), ("Tevul Yom", 4), ("Yadayim", 4), ("Oktzin", 3),
]

# Babylonian Talmud — (tractate, last_folio_number)
# Talmud folios run 2a, 2b, 3a, 3b … Xa, Xb
GEMARA = [
    ("Berakhot", 64), ("Shabbat", 157), ("Eruvin", 105), ("Pesachim", 121),
    ("Yoma", 88), ("Sukkah", 56), ("Beitzah", 40), ("Rosh Hashanah", 35),
    ("Taanit", 31), ("Megillah", 32), ("Moed Katan", 29), ("Hagigah", 27),
    ("Yevamot", 122), ("Ketubot", 112), ("Nedarim", 91), ("Nazir", 66),
    ("Sotah", 49), ("Gittin", 90), ("Kiddushin", 82), ("Bava Kamma", 119),
    ("Bava Metzia", 119), ("Bava Batra", 176), ("Sanhedrin", 113),
    ("Makkot", 24), ("Shevuot", 49), ("Avodah Zarah", 76), ("Horayot", 14),
    ("Zevachim", 120), ("Menachot", 110), ("Hullin", 142), ("Bekhorot", 61),
    ("Arakhin", 34), ("Temurah", 34), ("Keritot", 28), ("Meilah", 22),
    ("Tamid", 33), ("Niddah", 73),
]

# ── Helpers ─────────────────────────────────────────────────────────────────

_FOOTNOTE_RE = re.compile(r'<i class="footnote">.*?</i>', re.DOTALL)
_HTML_RE     = re.compile(r"<[^>]+>")


def flatten(obj):
    """Recursively join nested lists/strings, stripping HTML footnotes and tags."""
    if isinstance(obj, str):
        s = _FOOTNOTE_RE.sub("", obj)
        s = _HTML_RE.sub("", s)
        return " ".join(s.split())  # collapse whitespace
    if isinstance(obj, list):
        parts = [flatten(x) for x in obj if x]
        return " ".join(p for p in parts if p)
    return ""


def fetch_ref(sefaria_ref, retries=3):
    """
    Fetch a single Sefaria text ref.
    Returns (en_text, he_text) strings, or (None, None) on failure.
    sefaria_ref: e.g. "Genesis.1", "Mishnah_Berakhot.1", "Berakhot.2a"
    """
    url = f"{API_BASE}/{urllib.parse.quote(sefaria_ref)}?context=0&commentary=0"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Zeev/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            en = flatten(data.get("text") or data.get("he", ""))
            he = flatten(data.get("he", ""))
            # prefer English; if text is empty fall back to he
            if not en and he:
                en = he
            return en, he
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                return None, None


# ── Database setup ───────────────────────────────────────────────────────────

def init_db(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS done (
            ref TEXT PRIMARY KEY
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(
            source   UNINDEXED,
            ref      UNINDEXED,
            en,
            he       UNINDEXED,
            tokenize = 'unicode61'
        );
    """)
    con.commit()


# ── Import routines ──────────────────────────────────────────────────────────

def build_tanakh_refs():
    """Yield (source, human_ref, sefaria_ref) for every Tanakh chapter."""
    for book, chapters in TANAKH:
        for ch in range(1, chapters + 1):
            sref = f"{book.replace(' ', '_')}.{ch}"
            yield ("Tanakh", f"{book} {ch}", sref)


def build_mishna_refs():
    """Yield (source, human_ref, sefaria_ref) for every Mishna chapter."""
    for tractate, chapters in MISHNA:
        for ch in range(1, chapters + 1):
            sref = f"Mishnah_{tractate.replace(' ', '_')}.{ch}"
            yield ("Mishna", f"Mishna {tractate} {ch}", sref)


def build_gemara_refs():
    """Yield (source, human_ref, sefaria_ref) for every Gemara daf-side."""
    for tractate, last_folio in GEMARA:
        for folio in range(2, last_folio + 1):
            for side in ("a", "b"):
                sref = f"{tractate.replace(' ', '_')}.{folio}{side}"
                yield ("Gemara", f"{tractate} {folio}{side}", sref)


def import_corpus(refs, db_path, workers=3, rate_limit=4):
    """
    Download and insert refs into the DB.
    refs: iterable of (source, human_ref, sefaria_ref)
    rate_limit: max concurrent API requests
    """
    sem   = Semaphore(rate_limit)
    lock  = Lock()
    con   = sqlite3.connect(str(db_path))
    init_db(con)

    # Collect refs not yet done
    done  = {row[0] for row in con.execute("SELECT ref FROM done")}
    todo  = [(s, h, r) for s, h, r in refs if r not in done]
    total = len(todo)

    if total == 0:
        print("  all refs already imported, skipping.")
        con.close()
        return

    completed = [0]
    errors    = [0]

    def work(item):
        source, human_ref, sref = item
        with sem:
            en, he = fetch_ref(sref)
            time.sleep(0.25)  # polite rate limit
        return source, human_ref, sref, en, he

    print(f"  Downloading {total} refs with {workers} workers…")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, item): item for item in todo}
        for fut in as_completed(futures):
            source, human_ref, sref, en, he = fut.result()
            with lock:
                completed[0] += 1
                pct = 100 * completed[0] // total
                bar = "#" * (pct // 5) + "." * (20 - pct // 5)
                if en:
                    con.execute(
                        "INSERT INTO passages(source, ref, en, he) VALUES (?,?,?,?)",
                        (source, human_ref, en[:4000], he[:4000]),
                    )
                    con.execute("INSERT OR IGNORE INTO done(ref) VALUES (?)", (sref,))
                else:
                    errors[0] += 1
                con.commit()
                print(
                    f"\r  [{bar}] {pct:3d}% {completed[0]}/{total}  "
                    f"(errors: {errors[0]})",
                    end="", flush=True,
                )
    print()
    con.close()
    print(f"  Done. {completed[0] - errors[0]} inserted, {errors[0]} failed.")


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    corpora = {"tanakh", "mishna", "gemara"}
    for arg in sys.argv[1:]:
        if arg.startswith("--corpus"):
            val = arg.split("=", 1)[1] if "=" in arg else sys.argv[sys.argv.index(arg) + 1]
            corpora = {c.strip().lower() for c in val.split(",")}

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if "tanakh" in corpora:
        print("\n=== Tanakh (929 chapters) ===")
        import_corpus(build_tanakh_refs(), DB_PATH)

    if "mishna" in corpora:
        print("\n=== Mishna (525 chapters) ===")
        import_corpus(build_mishna_refs(), DB_PATH)

    if "gemara" in corpora:
        total_daf = sum((last - 1) * 2 for _, last in GEMARA)
        print(f"\n=== Babylonian Talmud (~{total_daf} daf-sides) ===")
        print("  This will take ~45 minutes. Ctrl-C to pause; re-run to resume.")
        import_corpus(build_gemara_refs(), DB_PATH)

    # Report final DB size
    size_mb = DB_PATH.stat().st_size / 1_048_576
    print(f"\nDone. torah.db is {size_mb:.1f} MB at {DB_PATH}")


if __name__ == "__main__":
    main()
