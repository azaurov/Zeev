import importlib.util, pathlib, sys, types

SRC = (pathlib.Path(__file__).resolve().parent.parent / "zeev" / "news_digest.py").read_text()


def _tavily_key():
    # Exec only the key-selection function; importing news_digest exits without env keys.
    start = SRC.index("def _tavily_key")
    end = SRC.index("TAVILY_API_KEY = _tavily_key()")
    ns = {"os": __import__("os")}
    exec(SRC[start:end], ns)
    return ns["_tavily_key"]


def test_digest_key_preferred():
    assert _tavily_key()({"TAVILY_DIGEST_KEY": "d", "TAVILY_API_KEY": "m"}) == "d"


def test_falls_back_to_shared_key():
    f = _tavily_key()
    assert f({"TAVILY_API_KEY": "m"}) == "m"
    assert f({"TAVILY_DIGEST_KEY": "", "TAVILY_API_KEY": "m"}) == "m"  # blank line must not win


def test_digest_request_uses_selected_key():
    assert '"api_key": TAVILY_API_KEY' in SRC and "TAVILY_API_KEY = _tavily_key()" in SRC
