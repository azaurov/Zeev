"""Agent (file-reading) history lives in its own table, yet both the main chat
and the agent can see it.

Kept out of `messages` for the same reason as `call_outcomes`/`dreams`: a
summary of a file gets embedded into history RAG and served back later as fact,
after the file has changed. Kept VISIBLE via a dated, capped prompt block so
"what did that file say?" still works from either side.
"""
import re
import time
from pathlib import Path

import pytest


@pytest.fixture
def db(zeev, tmp_path, monkeypatch):
    monkeypatch.setattr(zeev, "ZEEV_DB", tmp_path / "a.db")
    monkeypatch.setattr(zeev, "_db_con", None)
    yield zeev
    try:
        zeev._db_con.close()
    except Exception:
        pass
    zeev._db_con = None


def _count(z, table):
    with z._db_lock:
        return z._db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_empty_history_is_empty_block(db):
    assert db._agent_history_block() == ""


def test_round_trip_is_dated_and_caveated(db):
    db.append_agent_message("user", "what's the budget in the garden plan?")
    db.append_agent_message("assistant", "It lists 6173 dollars.")
    block = db._agent_history_block()
    assert "Alex: what's the budget" in block and "You: It lists 6173 dollars." in block
    assert re.search(r"\[\w{3} \w{3} \d+, \d+:\d\d [AP]M\]", block), block
    assert "may have changed" in block
    assert block.index("what's the budget") < block.index("6173")     # chronological


def test_agent_turns_never_reach_messages_or_rag(db):
    db.append_agent_message("user", "read plan.txt")
    db.append_agent_message("assistant", "budget 6173")
    assert _count(db, "messages") == 0
    assert _count(db, "message_vecs") == 0
    assert db.load_prior() == []


def test_main_chat_does_not_leak_into_agent_history(db):
    db.append_message("user", "tell me a joke")
    assert db._agent_history_block() == ""


def test_old_exchanges_expire(db):
    with db._db_lock:
        db._db().execute("INSERT INTO agent_messages (role, content, ts) VALUES (?,?,?)",
                         ("user", "ancient", time.time() - db._AGENT_HISTORY_WINDOW - 60))
        db._db().commit()
    db.append_agent_message("user", "fresh")
    block = db._agent_history_block()
    assert "fresh" in block and "ancient" not in block


def test_block_is_capped(db):
    for i in range(30):
        db.append_agent_message("assistant", "x" * 5000)
    block = db._agent_history_block()
    header_len = block.index("\n")
    assert len(block) - header_len <= db._AGENT_HISTORY_TOTAL_CHARS + 200
    assert all(len(l) <= db._AGENT_HISTORY_ITEM_CHARS + 40 for l in block.splitlines()[1:])


def test_fails_open_when_table_missing(db):
    with db._db_lock:
        db._db().execute("DROP TABLE agent_messages")
    assert db._agent_history_block() == ""


def test_agent_loop_sees_its_own_history_and_main_chat(zeev):
    seen = []

    def post(m):
        seen.append(m)
        return {"content": "ok"}, "fake"
    zeev.run_agent_loop([{"role": "user", "content": "main chat line"}], post=post,
                        prior_block="EARLIER-FILE-CONVO")
    assert "EARLIER-FILE-CONVO" in seen[0][0]["content"]
    assert any(m["content"] == "main chat line" for m in seen[0])
    seen.clear()
    zeev.run_agent_loop([{"role": "user", "content": "x"}], post=post)
    assert "EARLIER-FILE-CONVO" not in seen[0][0]["content"]


def test_main_chat_prompt_injects_agent_history(zeev):
    src = Path(zeev.__file__).read_text()
    body = src[src.index("def _build_system_prompt"):]
    body = body[:body.index("\ndef ", 10)]
    assert "_agent_history_block()" in body


def test_web_handler_routes_agent_turns_away_from_messages(zeev):
    src = Path(zeev.__file__).read_text()
    seg = src[src.index("_agent_turn = bool("):src.index("quantum_idea = extract_quantum_query")]
    # the ONLY write to the shared history in this stretch is the non-agent branch
    assert seg.count("append_message(") == 1 and seg.count("session.append(") == 1
    assert seg.index("append_agent_message(\"user\"") < seg.index("append_message(\"user\"")
    assert seg.count("append_agent_message(") == 2
    # history is read before this question is saved, so it can't quote itself
    assert seg.index("_agent_history_block()") < seg.index("append_agent_message(\"user\"")


# -- follow-ups ("read it again") ---------------------------------------------

@pytest.mark.parametrize("text", [
    "read it again", "Read that again please", "open the next one", "what does it say",
    "show me that one", "summarize it", "the other file", "what did that file say?",
])
def test_followup_phrasings_match(zeev, text):
    assert zeev._AGENT_FOLLOWUP_RE.search(text)


@pytest.mark.parametrize("text", [
    "what's the weather", "tell me a joke", "thanks", "I read it in a book",
    "how are you", "play that song again", "remind me to read it tonight",
])
def test_followup_ignores_ordinary_chat(zeev, text):
    assert not zeev._AGENT_FOLLOWUP_RE.search(text)


def test_followup_needs_a_fresh_agent_exchange(db):
    assert not db._agent_followup("read it again")            # no agent history at all
    db.append_agent_message("assistant", "README says hi")
    assert db._agent_followup("read it again")
    assert not db._agent_followup("what's the weather")       # right phrasing required too
    assert not db._agent_followup("read it again", now=time.time() + db._AGENT_FOLLOWUP_WINDOW + 60)


def test_followup_fails_closed_when_table_missing(db):
    with db._db_lock:
        db._db().execute("DROP TABLE agent_messages")
    assert db._agent_followup("read it again") is False


# -- stale recitation guard ---------------------------------------------------

def _tc(name, args):
    import json
    return {"id": "c", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_answer_without_tool_use_is_challenged_once(zeev, tmp_path, monkeypatch):
    (tmp_path / "plan.txt").write_text("budget: 999 dollars\n")
    monkeypatch.setenv("ZEEV_AGENT_ROOT", str(tmp_path))
    seen, script = [], iter([
        {"content": "It says budget 111 (from memory)."},                 # stale recital
        {"content": "", "tool_calls": [_tc("read_file", {"path": "plan.txt"})]},
        {"content": "It lists 999 dollars."},
    ])

    def post(m):
        seen.append(list(m))
        return next(script), "fake"
    reply, why = zeev.run_agent_loop([{"role": "user", "content": "read it again"}], post=post,
                                     prior_block="- Alex: read plan.txt\n- You: budget 111")
    assert (reply, why) == ("It lists 999 dollars.", "ok")
    assert "do not answer from memory" in seen[1][-1]["content"]
    assert len(seen) == 3


def test_challenge_happens_once_and_never_after_a_tool_call(zeev, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEEV_AGENT_ROOT", str(tmp_path))
    calls = []

    def stubborn(m):
        calls.append(1)
        return {"content": "no tools for me"}, "fake"
    assert zeev.run_agent_loop([{"role": "user", "content": "x"}], post=stubborn) == ("no tools for me", "ok")
    assert len(calls) == 2                       # one nudge, then its answer is accepted
    calls.clear()
    script = iter([{"content": "", "tool_calls": [_tc("list_dir", {})]}, {"content": "done"}])
    assert zeev.run_agent_loop([{"role": "user", "content": "x"}],
                               post=lambda m: (calls.append(1), (next(script), "fake"))[1]) == ("done", "ok")
    assert len(calls) == 2                       # tool used -> no nudge


def test_require_tool_can_be_disabled(zeev):
    n = []
    zeev.run_agent_loop([{"role": "user", "content": "x"}],
                        post=lambda m: (n.append(1), ({"content": "hi"}, "f"))[1], require_tool=False)
    assert len(n) == 1
