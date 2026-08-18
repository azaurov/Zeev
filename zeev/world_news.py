"""world_news.py — curated world-news snippet gathering ("the shpeel").

Shared by news_digest.py (the cron job that builds the cache zeev.py reads)
and zeev.py's own live fallback for when that cache is missing or stale.
Kept as its own module, the same way quantum.py is shared by quantum_daily.py/
quantum_convo.py and zeev.py, rather than duplicated: the curated query list
and prompt are exactly the kind of thing that drifts apart across two copies
if they're ever edited in only one place.
"""

import re

# Regions/topics chosen to surface stories outside the US/UK-centric
# mainstream feed -- a single generic "world news today" search just returns
# the same half-dozen top-of-fold wire stories every outlet already ran.
NEWS_QUERIES = [
    "Central Asia news this week Kazakhstan Uzbekistan Kyrgyzstan Tajikistan Turkmenistan",
    "West Africa news this week",
    "Caucasus news this week Georgia Armenia Azerbaijan",
    "Pacific Islands news this week",
    "Southeast Asia news this week Laos Cambodia Myanmar Timor-Leste",
    "Balkans news this week",
    "Latin America news this week underreported",
    "small country news overlooked by international media this week",
]

SHPEEL_PROMPT = """\
You are Zeev, giving Alex a spoken world-news roundup -- "the shpeel". Below \
are raw search snippets gathered from several under-covered regions and \
topics around the world.

Turn them into a spoken briefing: pick the {n} most interesting or \
significant stories, favoring ones that would NOT already be all over \
mainstream US/UK headlines. Skip anything that's just a wire-service rehash \
of a story everyone already knows, and skip anything that reads like an ad, \
spam, or a non-news result. Speak in first person, flowing prose (no bullet \
points, no headers, no markdown -- this gets read aloud by text-to-speech). \
Briefly name the country or region for each story so Alex has context. Keep \
it to about {words} words total.

RAW SNIPPETS:
{snippets}"""

_BAD_RESULT_MARKERS = ("search error", "unavailable", "no results found")

# qwen/qwen3.6-27b (the Groq fallback model both news_digest.py and
# news_probe.py use) inlines a <think>...</think> chain-of-thought block
# into `content` instead of keeping it separate -- the same issue zeev.py's
# _strip_think_text already works around for the main chat path (see its
# docstring / MODELS["2"] history). Found live 2026-08-18: the very first
# real cron-built digest got stored with its raw reasoning trace as the
# "summary", because news_digest.py is a standalone script that doesn't
# import zeev.py and had no equivalent stripping. Kept here, not duplicated
# in both scripts, since both call qwen3.6-27b through Groq.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_UNCLOSED_RE = re.compile(r"<think>.*", re.DOTALL)


def strip_think_text(text):
    if not text:
        return text
    return _THINK_UNCLOSED_RE.sub("", _THINK_BLOCK_RE.sub("", text)).strip()


# Each tavily_fn(query) call returns up to 5 results, each with a full page
# excerpt -- concatenated across 8 queries that ran to ~30-40KB raw and blew
# past Groq's request-size limit (413 Payload Too Large), while bosgame just
# timed out trying to process the same oversized prompt (found live
# 2026-08-18, first real cron run). Truncating each query's result blob
# keeps the combined prompt small enough for both backends without touching
# tavily_fn itself, since tavily_search() elsewhere is used for single-query
# live search where the full excerpt is exactly what's wanted.
_MAX_RESULT_CHARS = 1200


def gather_snippets(tavily_fn, queries=None):
    """Run each query through tavily_fn(query) -> str, concatenate the ones
    that came back with real content.

    A single failed or empty query degrades to fewer regions covered, not a
    crash or a summary built partly out of an error string -- the summarizer
    works fine on 5 of 8 regions, it just can't work on zero.
    """
    queries = queries if queries is not None else NEWS_QUERIES
    chunks = []
    for q in queries:
        try:
            result = tavily_fn(q)
        except Exception:
            continue
        if not result or any(m in result.lower() for m in _BAD_RESULT_MARKERS):
            continue
        if len(result) > _MAX_RESULT_CHARS:
            result = result[:_MAX_RESULT_CHARS] + "…"
        chunks.append(f"### {q}\n{result}")
    return "\n\n".join(chunks)


def summarize(snippets, llm_fn, n=6, words=300):
    """Turn already-gathered snippets into a spoken shpeel.

    Split out from build_shpeel() so news_digest.py can hang onto the raw
    `snippets` it gathered (news_probe.py's faithfulness grader needs the
    exact source text a given digest was built from, not a fresh re-fetch --
    news changes between the digest run and any later grading run).

    `llm_fn` takes a prompt string and returns (content, error) -- the same
    shape every LLM call site in this project already uses. Returns
    (content, error); on any failure content is None and error explains why.
    """
    if not snippets:
        return None, "no snippets gathered (search unavailable or all queries empty)"
    prompt = SHPEEL_PROMPT.format(n=n, words=words, snippets=snippets)
    content, err = llm_fn(prompt)
    if err:
        return None, err
    # Applied here rather than trusted to each llm_fn -- zeev.py's own
    # live-fallback llm_fn calls _groq_post directly (not _llm_complete, the
    # one wrapper that already strips this), so it has the identical gap
    # news_digest.py just hit live.
    return strip_think_text(content), None


def build_shpeel(tavily_fn, llm_fn, queries=None, n=6, words=300):
    """Gather snippets across the curated query list, then summarize them
    into a spoken shpeel. Returns (content, error); on any failure content is
    None and error explains why."""
    snippets = gather_snippets(tavily_fn, queries)
    return summarize(snippets, llm_fn, n=n, words=words)
