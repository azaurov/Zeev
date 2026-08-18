"""world_news.py's pure gather/summarize logic, independent of zeev.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "zeev"))
import world_news  # noqa: E402


def test_gather_skips_failed_and_empty_queries():
    def fake_tavily(q):
        if "Balkans" in q:
            raise RuntimeError("boom")
        if "Pacific" in q:
            return "No results found."
        return f"real content for {q}"

    queries = ["Balkans news", "Pacific Islands news", "West Africa news"]
    result = world_news.gather_snippets(fake_tavily, queries=queries)
    assert "West Africa" in result
    assert "Balkans" not in result
    assert "Pacific" not in result


def test_gather_truncates_oversized_results():
    """Regression for the live 2026-08-18 413 -- 8 queries x 5 full-length
    Tavily results blew past Groq's payload limit and timed out bosgame.
    Each query's blob must be capped before concatenation."""
    huge = "x" * 5000
    result = world_news.gather_snippets(lambda q: huge, queries=["Region A"])
    assert len(result) < len(huge)
    assert result.endswith("…")


def test_gather_does_not_truncate_short_results():
    short = "a normal-length snippet"
    result = world_news.gather_snippets(lambda q: short, queries=["Region A"])
    assert short in result
    assert not result.endswith("…")


def test_gather_returns_empty_string_when_all_queries_fail():
    result = world_news.gather_snippets(lambda q: "Search error: nope", queries=["a", "b"])
    assert result == ""


def test_build_shpeel_short_circuits_with_no_snippets():
    content, err = world_news.build_shpeel(
        lambda q: "Search unavailable: no key",
        lambda p: ("should not be called", None),
        queries=["a"],
    )
    assert content is None
    assert "no snippets" in err


def test_build_shpeel_propagates_llm_error():
    content, err = world_news.build_shpeel(
        lambda q: "some real content",
        lambda p: (None, "llm exploded"),
        queries=["a"],
    )
    assert content is None
    assert err == "llm exploded"


def test_strip_think_text_removes_closed_block():
    text = "<think>reasoning about the snippets here</think>Here's the shpeel."
    assert world_news.strip_think_text(text) == "Here's the shpeel."


def test_strip_think_text_removes_unclosed_block():
    text = "<think>reasoning that never closed because it hit the token budget"
    assert world_news.strip_think_text(text) == ""


def test_strip_think_text_leaves_normal_text_alone():
    text = "Here's the shpeel, no reasoning trace at all."
    assert world_news.strip_think_text(text) == text


def test_summarize_strips_think_blocks_from_llm_output():
    """Regression for the live 2026-08-18 first cron run: qwen3.6-27b's Groq
    fallback inlined its whole chain-of-thought into `content`, and got
    stored as the "summary" verbatim because nothing stripped it."""
    raw = "<think>let me pick the best stories</think>Here's what's happening around the world."
    content, err = world_news.summarize("some snippet", lambda p: (raw, None))
    assert err is None
    assert "<think>" not in content
    assert content == "Here's what's happening around the world."


def test_summarize_treats_empty_after_strip_as_truncation_error():
    """Regression for the live 2026-08-18 follow-up: after the <think>
    stripping fix landed, qwen3.6-27b spent its whole max_tokens budget
    reasoning across 8 regions and hit finish_reason length with an
    UNCLOSED <think> and zero real content -- stripping correctly reduced
    that to "", and an empty string must not be treated as a successful
    (if terse) digest."""
    raw = "<think>still reasoning about which stories to pick and never got to the answer"
    content, err = world_news.summarize("some snippet", lambda p: (raw, None))
    assert content is None
    assert "empty" in err.lower()


def test_summarize_is_reusable_with_already_gathered_snippets():
    """news_digest.py calls gather_snippets() and summarize() separately (not
    build_shpeel()) so it can persist the raw snippets alongside the summary
    -- news_probe.py's faithfulness grader needs the exact source text, not a
    fresh re-fetch of news that's since moved on."""
    content, err = world_news.summarize("some pre-gathered snippet text",
                                         lambda p: ("a summary", None))
    assert err is None
    assert content == "a summary"


def test_summarize_short_circuits_on_empty_snippets():
    content, err = world_news.summarize("", lambda p: ("should not be called", None))
    assert content is None
    assert "no snippets" in err


def test_build_shpeel_happy_path():
    seen_prompt = {}

    def fake_llm(prompt):
        seen_prompt["prompt"] = prompt
        return "here's the shpeel", None

    content, err = world_news.build_shpeel(
        lambda q: f"snippet for {q}",
        fake_llm,
        queries=["Region A news", "Region B news"],
    )
    assert err is None
    assert content == "here's the shpeel"
    assert "Region A news" in seen_prompt["prompt"]
    assert "Region B news" in seen_prompt["prompt"]
