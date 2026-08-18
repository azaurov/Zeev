"""World-news "shpeel" -- trigger gating and the cached/live-fallback shape.

get_shpeel() must never raise, must prefer a fresh cache over a live pull,
and must fall back sensibly when the cache is stale, missing, or the live
pull itself fails. The curated query/prompt logic lives in world_news.py and
is tested separately from zeev.py's caching/fallback wrapper around it.
"""
import time

import pytest


@pytest.fixture
def db(zeev, tmp_path, monkeypatch):
    monkeypatch.setattr(zeev, "ZEEV_DB", tmp_path / "d.db")
    monkeypatch.setattr(zeev, "_db_con", None)
    yield zeev
    try:
        zeev._db_con.close()
    except Exception:
        pass
    zeev._db_con = None


# --- trigger gating ---------------------------------------------------------

@pytest.mark.parametrize("text", [
    "give me the shpeel",
    "Give me the shpeel!",
    "what's the shpeel",
    "give me the spiel",
    "world news",
    "give me a world news briefing",
    "news roundup",
    "what's happening around the world",
    "catch me up on the world news",
    "catch me up on global news",
])
def test_shpeel_trigger_matches(zeev, text):
    assert zeev._SHPEEL_RE.search(text), f"should match: {text!r}"


@pytest.mark.parametrize("text", [
    "tell me a joke",
    "what's the weather",
    "remind me to check the news at four",
])
def test_shpeel_trigger_does_not_overmatch(zeev, text):
    assert not zeev._SHPEEL_RE.search(text), f"should not match: {text!r}"


def test_shpeel_regex_alone_overmatches_reminder_phrasing(zeev):
    """_SHPEEL_RE by itself DOES match this -- it's why every call site must
    also exclude _TOOL_INTENT_RE, not because the regex is meant to reject
    it. Documents the trap so a future edit doesn't silently drop the guard."""
    text = "remind me to check the world news briefing at nine"
    assert zeev._SHPEEL_RE.search(text)
    assert zeev._TOOL_INTENT_RE.search(text)


@pytest.mark.parametrize("text", [
    "remind me to check the world news briefing at nine",
    "remind me to give me the shpeel tomorrow",
])
def test_reminder_wins_over_shpeel_trigger(zeev, text):
    """The actual gate every call site uses: shpeel only fires when the
    tool-intent regex (reminders) doesn't also match. Regression test for
    the exact failure class CLAUDE.md documents for the goodnight gate and
    resolve_subject() -- a bare _SHPEEL_RE.search() would swallow this
    reminder request before it ever reaches the tool round trip."""
    assert not (zeev._SHPEEL_RE.search(text) and not zeev._TOOL_INTENT_RE.search(text))


# --- caching / fallback shape ------------------------------------------------

def test_no_cache_falls_through_to_live(db):
    """Nothing stored yet (fresh install, cron never ran) -- should attempt
    the live fallback rather than crash on a missing row."""
    z = db
    z._db()  # create tables
    monkeypatched = {}

    def fake_live():
        monkeypatched["called"] = True
        return "live shpeel content", None

    orig = z._shpeel_live_fallback
    z._shpeel_live_fallback = fake_live
    try:
        result = z.get_shpeel()
    finally:
        z._shpeel_live_fallback = orig

    assert monkeypatched.get("called")
    assert result == "live shpeel content"


def test_fresh_cache_is_used_without_live_fallback(db):
    z = db
    con = z._db()
    con.execute(
        "INSERT INTO world_news (content, ts) VALUES (?, ?)",
        ("cached shpeel", time.time()),
    )
    con.commit()

    called = {"n": 0}

    def fake_live():
        called["n"] += 1
        return "should not be used", None

    orig = z._shpeel_live_fallback
    z._shpeel_live_fallback = fake_live
    try:
        result = z.get_shpeel()
    finally:
        z._shpeel_live_fallback = orig

    assert result == "cached shpeel"
    assert called["n"] == 0, "fresh cache must not trigger a live pull"


def test_stale_cache_triggers_live_fallback(db):
    z = db
    con = z._db()
    stale_ts = time.time() - (z._SHPEEL_MAX_AGE_S + 3600)
    con.execute(
        "INSERT INTO world_news (content, ts) VALUES (?, ?)",
        ("old shpeel", stale_ts),
    )
    con.commit()

    orig = z._shpeel_live_fallback
    z._shpeel_live_fallback = lambda: ("fresh live shpeel", None)
    try:
        result = z.get_shpeel()
    finally:
        z._shpeel_live_fallback = orig

    assert result == "fresh live shpeel"


def test_stale_cache_used_when_live_fallback_fails(db):
    """A stale-but-present digest beats a flat apology when the live pull
    itself errors out (e.g. Tavily/Groq both down)."""
    z = db
    con = z._db()
    stale_ts = time.time() - (z._SHPEEL_MAX_AGE_S + 3600)
    con.execute(
        "INSERT INTO world_news (content, ts) VALUES (?, ?)",
        ("old but better than nothing", stale_ts),
    )
    con.commit()

    orig = z._shpeel_live_fallback
    z._shpeel_live_fallback = lambda: (None, "network down")
    try:
        result = z.get_shpeel()
    finally:
        z._shpeel_live_fallback = orig

    assert result == "old but better than nothing"


def test_no_cache_and_live_fallback_fails_gives_apology_not_crash(db):
    z = db
    z._db()

    orig = z._shpeel_live_fallback
    z._shpeel_live_fallback = lambda: (None, "network down")
    try:
        result = z.get_shpeel()
    finally:
        z._shpeel_live_fallback = orig

    assert isinstance(result, str) and result
