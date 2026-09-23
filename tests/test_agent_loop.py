"""Web agent loop: intent gate, bounded tool loop, and the free-provider chain."""
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "plan.txt").write_text("budget: 4242 dollars\n")
    monkeypatch.setenv("ZEEV_AGENT_ROOT", str(root))
    return root


def _call(name, args, i=0):
    return {"id": f"c{i}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def scripted(*msgs):
    """post() stand-in that replays canned assistant messages and records what it saw."""
    seen = []
    it = iter(msgs)

    def post(m):
        seen.append(list(m))
        try:
            return next(it), "fake"
        except StopIteration:
            return None, ""
    post.seen = seen
    return post


@pytest.mark.parametrize("text", [
    "what's in my workspace", "read the file plan.txt", "list the files in the projects folder",
    "search my files for the garden budget", "look in the folder for tax stuff",
    "summarize the document about the roof", "find the file with the wifi password",
    "read the README.txt file", "open budget.csv", "show me notes.md", "what's in plan.json",
    "read the README.txt in the ~/zeev-workspace/ folder",
    "tell me what's in the README.txt file", "tell me what\u2019s in the README.txt file",
    "What\u2018s in README.txt?", "what does the README.txt file say", "what's inside notes.md",
    "contents of budget.csv", "describe plan.json",
])
def test_intent_matches_file_requests(zeev, text):
    assert zeev._AGENT_INTENT_RE.search(zeev._agent_norm(text))   # pure: no DB read


@pytest.mark.parametrize("text", [
    "tell me about the Torah", "what's the weather", "how do I file my taxes",
    "find a way to relax", "what is in season in October", "can you read me the parsha",
    "I need to file a complaint", "check the weather in Canton",
    "I love reading my notes.txt", "read the docs at example.com", "show me a picture of a dog",
])
def test_intent_ignores_ordinary_chat(zeev, text):
    assert not zeev._AGENT_INTENT_RE.search(text)


def test_reminder_phrasing_wins_over_agent_gate(zeev):
    t = "remind me to read the file at four"
    assert zeev._AGENT_INTENT_RE.search(t) and zeev._TOOL_INTENT_RE.search(t)
    src = Path(zeev.__file__).read_text()
    gate = re.search(r"_agent_turn = bool\(AGENT_ENABLED and not _TOOL_INTENT_RE\.search\(user_msg\)\s+"
                     r"and _agent_intent\(user_msg\)\)", src)
    assert gate, "web /chat agent gate must exclude _TOOL_INTENT_RE"


def test_loop_reads_real_file_and_answers(zeev, ws):
    post = scripted(
        {"content": "", "tool_calls": [_call("search_files", {"query": "budget"})]},
        {"content": "", "tool_calls": [_call("read_file", {"path": "plan.txt"})]},
        {"content": "The plan lists a budget of 4242 dollars."},
    )
    steps = []
    reply, why = zeev.run_agent_loop([{"role": "user", "content": "budget?"}],
                                     on_step=lambda n, a: steps.append(n), post=post)
    assert (reply, why) == ("The plan lists a budget of 4242 dollars.", "ok")
    assert steps == ["search_files", "read_file"]
    # the model really was shown the file's contents, not a stub
    tool_msgs = [m for m in post.seen[-1] if m["role"] == "tool"]
    assert "4242" in tool_msgs[-1]["content"]


def test_loop_stops_at_step_cap(zeev, ws):
    forever = {"content": "", "tool_calls": [_call("list_dir", {})]}
    post = scripted(*[forever] * 20)
    reply, why = zeev.run_agent_loop([{"role": "user", "content": "x"}], post=post, max_steps=5)
    assert (reply, why) == (None, "step-cap")
    assert len(post.seen) == 5


def test_no_provider_and_empty_reply_are_reported_not_invented(zeev, ws):
    assert zeev.run_agent_loop([{"role": "user", "content": "x"}], post=scripted()) == (None, "no-provider")
    assert zeev.run_agent_loop([{"role": "user", "content": "x"}],
                               post=scripted({"content": "<think>hmm</think>"})) == (None, "empty")


def test_model_cannot_escape_workspace_through_the_loop(zeev, ws):
    post = scripted(
        {"content": "", "tool_calls": [_call("read_file", {"path": "../../../etc/passwd"})]},
        {"content": "could not read it"},
    )
    zeev.run_agent_loop([{"role": "user", "content": "x"}], post=post)
    tool_msg = [m for m in post.seen[-1] if m["role"] == "tool"][0]
    assert tool_msg["content"].startswith("Error:") and "root:" not in tool_msg["content"]


def test_unknown_tool_and_bad_json_do_not_crash(zeev, ws):
    post = scripted(
        {"content": "", "tool_calls": [_call("rm_rf", {"path": "/"}),
                                       {"id": "z", "function": {"name": "read_file", "arguments": "{oops"}}]},
        {"content": "sorry"},
    )
    reply, why = zeev.run_agent_loop([{"role": "user", "content": "x"}], post=post)
    assert why == "ok"
    results = [m["content"] for m in post.seen[-1] if m["role"] == "tool"]
    assert all(r.startswith("Error:") for r in results)


def test_history_is_trimmed_to_recent_turns(zeev, ws):
    hist = [{"role": "user", "content": f"m{i}"} for i in range(40)]
    post = scripted({"content": "done"})
    zeev.run_agent_loop(hist, post=post)
    sent = post.seen[0]
    assert sent[0]["role"] == "system" and len(sent) == 1 + zeev._AGENT_HISTORY_MSGS


# -- provider chain ---------------------------------------------------------

def _resp(status, msg=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"choices": [{"message": msg or {}}]}
    return r


def test_chain_falls_through_groq_429_to_cloudflare(zeev, monkeypatch):
    order = []
    monkeypatch.setattr(zeev, "GROQ_API_KEY", "x")
    monkeypatch.setattr(zeev, "CLOUDFLARE_AI_URL", "https://cf.example/v1")
    monkeypatch.setattr(zeev, "REQUESTY_API_KEY", "x")
    monkeypatch.setattr(zeev, "_groq_post", lambda *a, **k: (order.append("groq"), (_resp(429), None))[1])

    def compat(url, key, msgs, model, stream, max_tokens, tools=None):
        assert tools, "agent chain must send the tool schema"
        assert stream is False
        order.append("cloudflare" if "cf.example" in url else "requesty")
        return (_resp(200, {"content": "hi"}), None) if "cf.example" in url else (_resp(500), None)

    monkeypatch.setattr(zeev, "_openai_compat_post", compat)
    msg, label = zeev._agent_post([{"role": "user", "content": "x"}])
    assert (msg, label) == ({"content": "hi"}, "cloudflare")
    assert order == ["groq", "cloudflare"]


def test_chain_reports_none_when_every_provider_fails(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "GROQ_API_KEY", "x")
    monkeypatch.setattr(zeev, "CLOUDFLARE_AI_URL", "")
    monkeypatch.setattr(zeev, "REQUESTY_API_KEY", "")
    monkeypatch.setattr(zeev, "_groq_post", lambda *a, **k: (None, "boom"))
    assert zeev._agent_post([{"role": "user", "content": "x"}]) == (None, "")


def test_chain_never_uses_the_shared_quota_providers(zeev):
    src = Path(zeev.__file__).read_text()
    body = src[src.index("def _agent_post"):src.index("def run_agent_loop")]
    assert "OPENROUTER" not in body and "FREEAI" not in body


def test_compat_post_forwards_tools(zeev, monkeypatch):
    captured = {}
    monkeypatch.setattr(zeev.requests, "post",
                        lambda url, json=None, **k: captured.update(json) or _resp(200))
    zeev._openai_compat_post("u", "k", [], "m", False, 10, tools=[{"type": "function"}])
    assert captured["tools"] == [{"type": "function"}] and captured["tool_choice"] == "auto"
    captured.clear()
    zeev._openai_compat_post("u", "k", [], "m", False, 10)
    assert "tools" not in captured        # existing chat callers are unchanged
