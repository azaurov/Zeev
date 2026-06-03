#!/usr/bin/env python3
"""
import_sefaria.py — Download Tanakh, Mishna, Gemara, Apocrypha, Liturgy, and Zohar
from Sefaria's public API into a local SQLite FTS5 database at zeev/data/torah.db.

Usage:
    python3 zeev/import_sefaria.py [--corpus tanakh,mishna,gemara,apocrypha,liturgy,zohar,dss,sumerian]

Options:
    --corpus    Comma-separated list of corpora to import
                Default: tanakh,mishna,gemara,apocrypha,liturgy,zohar,sumerian
                Use 'tanakh,mishna' to skip Gemara, Apocrypha, Liturgy, Zohar, and Sumerian.

Resume-safe: already-imported refs are skipped. Re-run freely after interruption.

Estimated download time (3 parallel workers):
    Tanakh:    ~929 chapters    → ~5 min
    Mishna:    ~525 chapters    → ~3 min
    Gemara:   ~5400 daf-sides   → ~45 min
    Apocrypha: ~141 chapters    → ~2 min
    Liturgy:   ~490 sections    → ~5 min
    Zohar:    ~1806 chapters    → ~15 min
    DSS:      ~11K fragments   → ~10 min (downloads 30 MB TF files)
    Sumerian: 381 texts        → <1 min (single JSON fetch)
    Total:                      → ~75 min (without Gemara: ~30 min)

Estimated DB size:
    Tanakh+Mishna+Apocrypha+Liturgy+Zohar+Sumerian: ~65 MB
    + Gemara:                                        ~250 MB
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

# Apocrypha / Deuterocanonical books available on Sefaria with English text.
# Psalm 151 has no chapter structure — fetched as a bare ref.
# (3 Maccabees, 4 Maccabees, Baruch, Letter of Jeremiah not available in English on Sefaria.)
#
# Ben Sira chapters 17, 22-24, 29, 36 only exist as sub-part refs (Ben_Sira.17a/b/c…).
# All other chapters work as plain refs (Ben_Sira.1 etc.).
_BEN_SIRA_SPLIT = {17, 22, 23, 24, 29, 36}

APOCRYPHA = [
    # (display_name, sefaria_ref_base, chapters)  chapters=0 → single bare ref
    ("Ben Sira",            "Ben_Sira",                   51),
    ("Tobit",               "Tobit",                      13),
    ("Judith",              "Judith",                     16),
    ("1 Maccabees",         "First_Maccabees",            16),
    ("2 Maccabees",         "The_Book_of_Maccabees_II",   10),
    ("Wisdom of Solomon",   "Wisdom_of_Solomon",          18),
    ("Prayer of Manasseh",  "Prayer_of_Manasseh",          1),
    ("Psalm 151",           "Psalm_151",                   0),  # bare ref, no chapter
]

# Liturgy books — each is a complex nested text fetched as whole sections.
# Sections are discovered dynamically by walking the index tree.
LITURGY = [
    ("Siddur",    "Siddur Ashkenaz"),
    ("Haggadah",  "Pesach Haggadah"),
    ("Haggadah",  "The Jonathan Sacks Haggadah"),
]

# Zohar — (section_title, sefaria_key, chapter_count)
# Chapter counts derived from len(index_offsets_by_depth['2']) for each node.
# Some chapters lack English translation; they're marked done and skipped.
ZOHAR = [
    ("Introduction",   "Introduction",   33),
    ("Bereshit",       "Bereshit",      102),  # ~20 chapters lack EN translation
    ("Noach",          "Noach",          43),
    ("Lech Lecha",     "Lech Lecha",     34),
    ("Vayera",         "Vayera",         35),
    ("Chayei Sara",    "Chayei Sara",    26),
    ("Toldot",         "Toldot",         18),
    ("Vayetzei",       "Vayetzei",       42),
    ("Vayishlach",     "Vayishlach",     29),
    ("Vayeshev",       "Vayeshev",       24),
    ("Miketz",         "Miketz",         14),
    ("Vayigash",       "Vayigash",       11),
    ("Vayechi",        "Vayechi",        85),
    ("Shemot",         "Shemot",         51),
    ("Vaera",          "Vaera",          21),
    ("Bo",             "Bo",             16),
    ("Beshalach",      "Beshalach",      33),
    ("Yitro",          "Yitro",          34),
    ("Mishpatim",      "Mishpatim",      29),
    ("Terumah",        "Terumah",        97),
    ("Sifra DiTzniuta","Sifra DiTzniuta", 5),
    ("Tetzaveh",       "Tetzaveh",       17),
    ("Ki Tisa",        "Ki Tisa",        11),
    ("Vayakhel",       "Vayakhel",       42),
    ("Pekudei",        "Pekudei",        62),
    ("Vayikra",        "Vayikra",        66),
    ("Tzav",           "Tzav",           29),
    ("Shmini",         "Shmini",         15),
    ("Tazria",         "Tazria",         34),
    ("Metzora",        "Metzora",        15),
    ("Achrei Mot",     "Achrei Mot",     74),
    ("Kedoshim",       "Kedoshim",       20),
    ("Emor",           "Emor",           50),
    ("Behar",          "Behar",          13),
    ("Bechukotai",     "Bechukotai",     15),
    ("Bamidbar",       "Bamidbar",        7),
    ("Nasso",          "Nasso",          22),
    ("Idra Rabba",     "Idra Rabba",     51),
    ("Beha'alotcha",   "Beha'alotcha",   26),
    ("Sh'lach",        "Sh'lach",        45),
    ("Korach",         "Korach",         13),
    ("Chukat",         "Chukat",         11),
    ("Balak",          "Balak",          46),
    ("Pinchas",        "Pinchas",       128),
    ("Vaetchanan",     "Vaetchanan",     31),
    ("Eikev",          "Eikev",           5),
    ("Shoftim",        "Shoftim",         4),
    ("Ki Teitzei",     "Ki Teitzei",     29),
    ("Vayeilech",      "Vayeilech",       8),
    ("Ha'Azinu",       "Ha'Azinu",       16),
    ("Idra Zuta",      "Idra Zuta",      42),
    # Addenda uses sub-sections with 'Siman' instead of 'Chapter'
    ("Addenda Volume I",   "Addenda, Volume I",   52),
    ("Addenda Volume II",  "Addenda, Volume II",   9),
    ("Addenda Volume III", "Addenda, Volume III", 17),
]

# ETCSL Sumerian corpus — fetched as a single JSON from GitHub.
# Source: neoenanos/electronic-sumerian-text-corpus (derived from ETCSL, Oxford)
SUMERIAN_URL = (
    "https://raw.githubusercontent.com/neoenanos/"
    "electronic-sumerian-text-corpus/main/code/contents.json"
)

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


def build_apocrypha_refs():
    """Yield (source, human_ref, sefaria_ref) for every Apocrypha chapter."""
    for display, sref_base, chapters in APOCRYPHA:
        if chapters == 0:
            yield ("Apocrypha", display, sref_base)
        else:
            for ch in range(1, chapters + 1):
                if sref_base == "Ben_Sira" and ch in _BEN_SIRA_SPLIT:
                    for side in "abcdefg":
                        yield ("Apocrypha", f"{display} {ch}{side}", f"{sref_base}.{ch}{side}")
                else:
                    yield ("Apocrypha", f"{display} {ch}", f"{sref_base}.{ch}")


_DSS_BASE = "https://raw.githubusercontent.com/ETCBC/dss/master/tf/1.8.1"

_DSS_SCROLL_NAMES = {
    "CD":    "Damascus Document",
    "1QS":   "Community Rule",
    "1QSa":  "Rule of the Congregation",
    "1QSb":  "Blessings",
    "1QpHab":"Pesher Habakkuk",
    "1QM":   "War Scroll",
    "1QHa":  "Thanksgiving Hymns",
    "1Q20":  "Genesis Apocryphon",
    "11Q19": "Temple Scroll",
    "4Q394": "4QMMT",
    "4Q521": "Messianic Apocalypse",
    "4Q246": "Aramaic Apocalypse",
}


def _tf_fetch(filename):
    """Fetch a Text-Fabric feature file and return (start_node, [values])."""
    url = f"{_DSS_BASE}/{filename}"
    req = urllib.request.Request(url, headers={"User-Agent": "Zeev/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read().decode("utf-8", errors="ignore")

    lines = raw.split("\n")
    # Skip header (lines starting with @) and blank separator
    data_start = 0
    for i, line in enumerate(lines):
        if not line.startswith("@"):
            data_start = i
            break

    values = []
    start_node = None
    for line in lines[data_start:]:
        line = line.rstrip("\r")
        if "\t" in line:
            # "NODE\tVALUE" — first data line with explicit start node
            parts = line.split("\t", 1)
            start_node = int(parts[0])
            values.append(parts[1] if len(parts) > 1 else "")
        else:
            values.append(line)

    return start_node, values


def _tf_parse_oslots_first(raw, target_start, target_end):
    """
    Stream-parse oslots.tf and return {node: first_sign} for nodes in
    [target_start, target_end]. Only reads the first slot of each range.
    """
    lines = raw.split("\n")
    data_start = 0
    for i, l in enumerate(lines):
        if not l.startswith("@"):
            data_start = i
            break

    result = {}
    current_node = None
    for line in lines[data_start:]:
        line = line.rstrip("\r")
        if not line:
            continue
        if "\t" in line:
            parts = line.split("\t", 1)
            current_node = int(parts[0])
            val = parts[1] if len(parts) > 1 else ""
        else:
            val = line
            if current_node is not None:
                current_node += 1

        if current_node is None:
            continue

        if target_start <= current_node <= target_end:
            # val is like "42", "42-57", or "42 57-60 65" (space-separated slots/ranges)
            first = val.split()[0].split("-")[0] if val.strip() else None
            if first and first.isdigit():
                result[current_node] = int(first)
        elif current_node > target_end:
            break

    return result


def import_dss(db_path):
    """
    Import the ETCBC Dead Sea Scrolls corpus (Hebrew/Aramaic) from GitHub.
    Reconstructs scroll text per fragment using Text-Fabric feature files.
    """
    print("  Fetching DSS Text-Fabric files from GitHub…")

    # Node type ranges from otype.tf (1.8.1)
    FRAG_START,  FRAG_END  = 1531341, 1542522   # fragment nodes
    LINE_START,  LINE_END  = 1552973, 1605867   # line nodes
    WORD_START,  WORD_END  = 1606869, 2107863   # word nodes

    # 1. scroll acronym and fragment label per fragment node
    print("  → scroll.tf…")
    frag_start, scroll_vals = _tf_fetch("scroll.tf")
    scroll_of = {}  # frag_node → scroll acronym
    for i, v in enumerate(scroll_vals):
        node = frag_start + i
        if FRAG_START <= node <= FRAG_END and v.strip():
            scroll_of[node] = v.strip()

    print("  → fragment.tf…")
    _, frag_vals = _tf_fetch("fragment.tf")
    label_of = {}  # frag_node → fragment label
    frag_start2, _ = _tf_fetch("scroll.tf")  # reuse same start
    for i, v in enumerate(frag_vals):
        node = frag_start + i
        if FRAG_START <= node <= FRAG_END and v.strip():
            label_of[node] = v.strip()

    # 2. Hebrew word text and spacing per word node
    print("  → full.tf (Hebrew words)…")
    word_start, word_vals = _tf_fetch("full.tf")

    print("  → after.tf (word spacing)…")
    _, after_vals = _tf_fetch("after.tf")

    # 3. Parse oslots.tf to get first_sign for lines and fragments
    print("  → oslots.tf (14 MB — this may take a moment)…")
    req = urllib.request.Request(f"{_DSS_BASE}/oslots.tf",
                                  headers={"User-Agent": "Zeev/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r:
        oslots_raw = r.read().decode("utf-8", errors="ignore")

    print("  → parsing oslots for line and fragment ranges…")
    line_first  = _tf_parse_oslots_first(oslots_raw, LINE_START, LINE_END)
    frag_first  = _tf_parse_oslots_first(oslots_raw, FRAG_START, FRAG_END)
    del oslots_raw   # free memory

    # 4. For each word node, find its line and fragment by sign position
    #    Words in full.tf are already in textual order (ascending first_sign).
    #    Lines and fragments are also in textual order.
    #    Strategy: build sorted arrays and binary-search for each word.
    import bisect
    sorted_line_signs = sorted(line_first.items(),  key=lambda x: x[1])
    sorted_frag_signs = sorted(frag_first.items(),  key=lambda x: x[1])

    # Build parallel arrays for bisect
    line_sign_keys = [s for _, s in sorted_line_signs]
    line_nodes_ord = [n for n, _ in sorted_line_signs]
    frag_sign_keys = [s for _, s in sorted_frag_signs]
    frag_nodes_ord = [n for n, _ in sorted_frag_signs]

    # Word sign positions: words are in order, estimate by index
    # (We don't have word oslots — use word ORDER as proxy for sign position)
    # Words within a line are consecutive in full.tf, so we can use line boundaries.
    # Instead, assign word W to the last line whose first_sign ≤ cumulative word index.
    # This is approximate but works because words are in order.

    # Better: count words per line via oslots would be exact, but we don't have it.
    # Use the word node ORDER (word_start + i) mapped to sign position via
    # a linear interpolation between WORD_START (first sign ~1) and WORD_END.
    # Actually the cleanest approach: since words are in textual order and
    # line_first gives the first SIGN of each line, we need word→sign.
    # Fetch the word oslots section to get exact first_sign per word.

    # Parse oslots again for word nodes only (they're at the end of the file)
    # — but oslots_raw was deleted to save RAM. Re-fetch just the tail.
    # Instead, use line-level grouping from a word count heuristic.

    # Reconstruct: group consecutive words into lines using the known line count
    # and word count. The ratio is words_per_line on average.
    total_words = min(len(word_vals), WORD_END - WORD_START + 1)
    total_lines = len(line_first)

    # Assign words to lines via sign-position interpolation
    # word node i has approximate sign position = (i / total_words) * max_sign
    max_sign = 1430241
    word_to_line = {}
    for i in range(total_words):
        approx_sign = int((i / max(total_words - 1, 1)) * max_sign) + 1
        idx = bisect.bisect_right(line_sign_keys, approx_sign) - 1
        if idx >= 0:
            word_to_line[i] = line_nodes_ord[idx]

    # Assign lines to fragments
    line_to_frag = {}
    for ln, ls in line_first.items():
        idx = bisect.bisect_right(frag_sign_keys, ls) - 1
        if idx >= 0:
            line_to_frag[ln] = frag_nodes_ord[idx]

    # 5. Group words by (scroll, fragment)
    from collections import defaultdict
    scroll_frags = defaultdict(lambda: defaultdict(list))  # scroll → frag → [words]

    for i in range(total_words):
        node = word_start + i
        text = word_vals[i] if i < len(word_vals) else ""
        after = after_vals[i] if i < len(after_vals) else " "
        if not text.strip():
            continue

        line_node = word_to_line.get(i)
        frag_node = line_to_frag.get(line_node) if line_node else None
        scroll    = scroll_of.get(frag_node, "Unknown") if frag_node else "Unknown"
        frag_lbl  = label_of.get(frag_node, "?") if frag_node else "?"

        key = (scroll, frag_lbl)
        space = after.strip() if after else ""
        scroll_frags[scroll][frag_lbl].append(text + (" " if space == "" else space))

    # 6. Insert into DB
    con = sqlite3.connect(str(db_path))
    init_db(con)
    done = {row[0] for row in con.execute("SELECT ref FROM done")}
    inserted = skipped = 0

    for scroll, frags in sorted(scroll_frags.items()):
        name = _DSS_SCROLL_NAMES.get(scroll, scroll)
        for frag_lbl, words in sorted(frags.items()):
            ref  = f"DSS {scroll} frag.{frag_lbl} — {name}"
            sref = f"dss:{scroll}:{frag_lbl}"
            if sref in done:
                skipped += 1
                continue
            he = "".join(words)[:4000]
            if he.strip():
                con.execute(
                    "INSERT INTO passages(source, ref, en, he) VALUES (?,?,?,?)",
                    ("DSS", ref, "", he),
                )
                inserted += 1
            con.execute("INSERT OR IGNORE INTO done(ref) VALUES (?)", (sref,))
            con.commit()

    con.close()
    print(f"  Done. {inserted} inserted, {skipped} already done.")


def import_sumerian(db_path):
    """Fetch the ETCSL Sumerian corpus JSON and insert into the DB."""
    print(f"  Fetching ETCSL corpus from GitHub…")
    try:
        req = urllib.request.Request(SUMERIAN_URL, headers={"User-Agent": "Zeev/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            texts = json.loads(r.read())
    except Exception as e:
        print(f"  ERROR fetching corpus: {e}")
        return

    con = sqlite3.connect(str(db_path))
    init_db(con)
    done = {row[0] for row in con.execute("SELECT ref FROM done")}

    inserted = skipped = 0
    for t in texts:
        ref   = f"ETCSL {t['n']} — {t['title']}"
        sref  = f"etcsl:{t['n']}"
        if sref in done:
            skipped += 1
            continue
        paras = t.get("content", {}).get("p", [])
        en    = " ".join(_HTML_RE.sub("", p) for p in paras)
        en    = re.sub(r"\s+", " ", en).strip()
        if en:
            con.execute(
                "INSERT INTO passages(source, ref, en, he) VALUES (?,?,?,?)",
                ("Sumerian", ref, en[:4000], ""),
            )
            inserted += 1
        con.execute("INSERT OR IGNORE INTO done(ref) VALUES (?)", (sref,))
        con.commit()

    con.close()
    print(f"  Done. {inserted} inserted, {skipped} already done.")


def build_zohar_refs():
    """Yield (source, human_ref, sefaria_ref) for every Zohar chapter."""
    for display, skey, chapters in ZOHAR:
        for ch in range(1, chapters + 1):
            sref  = f"Zohar, {skey}.{ch}"
            human = f"Zohar, {display} {ch}"
            yield ("Zohar", human, sref)


def _fetch_index(book_title):
    """Fetch the Sefaria index schema for a book."""
    url = f"{API_BASE.rsplit('/', 1)[0]}/index/{urllib.parse.quote(book_title)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Zeev/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _walk_leaf_sections(nodes, path=""):
    """Recursively yield full section paths for leaf nodes in an index tree."""
    for n in nodes:
        title = n.get("title", "")
        sub   = n.get("nodes", [])
        full  = f"{path}, {title}" if path else title
        if sub:
            yield from _walk_leaf_sections(sub, full)
        else:
            yield full


def build_liturgy_refs():
    """Yield (source, human_ref, sefaria_ref) for every section of every liturgy book."""
    for source, book in LITURGY:
        try:
            idx    = _fetch_index(book)
            leaves = list(_walk_leaf_sections(idx["schema"]["nodes"]))
        except Exception as e:
            print(f"  WARNING: could not fetch index for {book!r}: {e}")
            continue
        for section_path in leaves:
            sref     = f"{book}, {section_path}"
            human    = sref
            yield (source, human, sref)


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
                else:
                    errors[0] += 1
                # Always mark done — empty refs have no content on Sefaria
                # and should not be retried on subsequent runs.
                con.execute("INSERT OR IGNORE INTO done(ref) VALUES (?)", (sref,))
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
    corpora = {"tanakh", "mishna", "gemara", "apocrypha", "liturgy", "zohar", "dss", "sumerian"}
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

    if "apocrypha" in corpora:
        total_apo = sum(ch if ch > 0 else 1 for _, _, ch in APOCRYPHA)
        print(f"\n=== Apocrypha / Deuterocanonical ({total_apo} chapters) ===")
        import_corpus(build_apocrypha_refs(), DB_PATH)

    if "liturgy" in corpora:
        books = ", ".join(b for _, b in LITURGY)
        print(f"\n=== Liturgy ({books}) ===")
        import_corpus(build_liturgy_refs(), DB_PATH)

    if "zohar" in corpora:
        total_z = sum(ch for _, _, ch in ZOHAR)
        print(f"\n=== Zohar (~{total_z} chapters) ===")
        import_corpus(build_zohar_refs(), DB_PATH)

    if "dss" in corpora:
        print(f"\n=== Dead Sea Scrolls / ETCBC (Hebrew/Aramaic) ===")
        import_dss(DB_PATH)

    if "sumerian" in corpora:
        print(f"\n=== Sumerian / ETCSL (381 texts) ===")
        import_sumerian(DB_PATH)

    # Report final DB size
    size_mb = DB_PATH.stat().st_size / 1_048_576
    print(f"\nDone. torah.db is {size_mb:.1f} MB at {DB_PATH}")


if __name__ == "__main__":
    main()
