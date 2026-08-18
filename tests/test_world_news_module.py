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
