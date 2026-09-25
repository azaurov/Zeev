"""Per-user data isolation: Maria's memory must never bleed into Alex's.

The failure class here is SILENT: nothing raises when Maria's fact lands in
Alex's file, the prompt just quietly says "Alex has ..." about her. So these
tests assert where rows physically ended up (which SQLite file), and what a
real prompt contains -- not that a call returned.
"""
import re
import sqlite3
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "zeev"))
import userctx  # noqa: E402


@pytest.fixture
def mu(zeev, tmp_path, monkeypatch):
    """Point Zeev at a scratch data dir with fresh per-user state."""
    monkeypatch.setattr(zeev, "ZEEV_DB", tmp_path / "zeev.db")
    monkeypatch.setattr(zeev, "_db_con", None)
    monkeypatch.setattr(zeev, "_user_dbs", {})
    monkeypatch.setattr(zeev, "_user_state", {})
    monkeypatch.setattr(zeev, "USER_FACTS", zeev._UserList("facts", zeev.load_memory))
    monkeypatch.setattr(zeev, "USER_NOTES", zeev._UserList("notes", zeev.load_notes))
    monkeypatch.setattr(zeev, "_DEVICE_USER", [userctx.DEFAULT_USER])
    monkeypatch.setattr(zeev, "_DEVICE_USER_LAST", [0.0])
    monkeypatch.setattr(zeev, "_DEVICE_SESSIONS", {})
    # No network in any prompt build.
    monkeypatch.setattr(zeev, "retrieve_semantic", lambda *a, **k: None)
    monkeypatch.setattr(zeev, "ambient_place", lambda *a, **k: "")
    yield zeev
    for con in list(zeev._user_dbs.values()):
        con.close()
    if zeev._db_con is not None:
        zeev._db_con.close()


def _rows(path, table):
    con = sqlite3.connect(str(path))
    try:
        return [tuple(r) for r in con.execute(f"SELECT * FROM {table}")]
    finally:
        con.close()


# --- storage ---------------------------------------------------------------

def test_maria_messages_land_in_her_own_file_not_alexs(mu, tmp_path):
    with userctx.as_user("alex"):
        mu.append_message("user", "alex-secret-message")
    with userctx.as_user("maria"):
        mu.append_message("user", "maria-private-message")

    alex = [r[2] for r in _rows(tmp_path / "zeev.db", "messages")]
    maria_path = tmp_path / "users" / "maria" / "zeev.db"
    maria = [r[2] for r in _rows(maria_path, "messages")]
    assert alex == ["alex-secret-message"]
    assert maria == ["maria-private-message"]


def test_alexs_data_stays_in_the_original_file(mu, tmp_path):
    """Nothing moved: the default user is still data/zeev.db."""
    mu.append_message("user", "hello")
    assert [r[2] for r in _rows(tmp_path / "zeev.db", "messages")] == ["hello"]
    assert not (tmp_path / "users").exists()


def test_facts_are_per_user_and_never_cross(mu):
    with userctx.as_user("alex"):
        mu.USER_FACTS[:] = ["Alex keeps bees."]
        mu.save_memory(list(mu.USER_FACTS))
    with userctx.as_user("maria"):
        assert list(mu.USER_FACTS) == []          # not Alex's
        mu.USER_FACTS[:] = ["Maria paints."]
        mu.save_memory(list(mu.USER_FACTS))
    with userctx.as_user("alex"):
        assert list(mu.USER_FACTS) == ["Alex keeps bees."]
        assert mu.load_memory() == ["Alex keeps bees."]
    with userctx.as_user("maria"):
        assert mu.load_memory() == ["Maria paints."]


def test_reminders_notes_and_agent_history_are_per_user(mu):
    with userctx.as_user("maria"):
        mu.add_reminder_at("water the ferns", 1.0)
        mu.add_note("maria note")
        mu.append_agent_message("user", "read maria.txt")
    with userctx.as_user("alex"):
        assert mu.list_reminders() == []
        assert mu.load_notes() == []
        assert "maria.txt" not in mu._agent_history_block()
    with userctx.as_user("maria"):
        assert [r["text"] for r in mu.list_reminders()] == ["water the ferns"]


def test_shared_tables_stay_household_wide(mu, tmp_path):
    """world_news / quantum / settings are NOT copied per user (an empty copy
    would make every Maria turn re-fetch the news live)."""
    with userctx.as_user("maria"):
        mu.save_quantum_insight("idea", {}, {}, "insight")
    assert len(_rows(tmp_path / "zeev.db", "quantum_insights")) == 1
    with userctx.as_user("alex"):
        assert mu.load_quantum_insights(k=3)


# --- the prompt ------------------------------------------------------------

def test_alexs_facts_and_notes_never_reach_marias_prompt(mu):
    with userctx.as_user("alex"):
        mu.USER_FACTS[:] = ["Alex is allergic to walnuts."]
        mu.save_memory(list(mu.USER_FACTS))
        mu.add_note("alex birthday gift idea")
    with userctx.as_user("maria"):
        prompt = mu._build_system_prompt("what do you know about me?")
    assert "walnuts" not in prompt
    assert "birthday gift idea" not in prompt
    assert "You are talking to Maria" in prompt
    assert "You are talking to Alex." not in prompt


def test_marias_facts_reach_only_marias_prompt(mu):
    with userctx.as_user("maria"):
        mu.USER_FACTS[:] = ["Maria grows tomatoes."]
        mu.save_memory(list(mu.USER_FACTS))
        assert "tomatoes" in mu._build_system_prompt("hello")
        assert "## What I know about Maria:" in mu._build_system_prompt("hello")
    with userctx.as_user("alex"):
        assert "tomatoes" not in mu._build_system_prompt("hello")
        assert "## What I know about Alex:" not in mu._build_system_prompt("hello")


def test_calendar_is_alexs_and_is_not_served_to_maria(mu, monkeypatch):
    monkeypatch.setattr(mu, "gcal_fetch", lambda days=1: "Alex: dentist 3pm")
    with userctx.as_user("maria"):
        prompt = mu._build_system_prompt("what's on my calendar today?")
    assert "dentist" not in prompt
    assert "No calendar is connected for Maria" in prompt


# --- the background-thread race (the silent one) ---------------------------

def test_spawned_thread_keeps_the_turns_user(mu):
    """extract_memory runs in a background thread. A bare Thread starts with
    the DEFAULT user's context, so Maria's extraction would be filed under
    Alex's facts. userctx.spawn must carry Maria along."""
    def save():
        mu.save_memory(["a fact from Maria's turn"])

    with userctx.as_user("maria"):
        userctx.spawn(save).join(5)
    with userctx.as_user("alex"):
        assert mu.load_memory() == []
    with userctx.as_user("maria"):
        assert mu.load_memory() == ["a fact from Maria's turn"]


def test_bare_thread_would_have_leaked_into_alex(mu):
    """Documents WHY spawn exists: this is the bug it prevents."""
    def save():
        mu.save_memory(["a fact from Maria's turn"])

    with userctx.as_user("maria"):
        t = threading.Thread(target=save)
        t.start(); t.join(5)
    with userctx.as_user("alex"):
        assert mu.load_memory() == ["a fact from Maria's turn"]   # the leak


def test_turn_end_background_jobs_use_spawn(zeev):
    """Structural: the three per-turn background jobs must not be bare Threads."""
    import inspect
    src = inspect.getsource(zeev._handle_transcript)
    for job in ("_bg_index", "_bg_memorize", "_prefetch_detail"):
        assert f"userctx.spawn({job})" in src, f"{job} must keep the turn's user"
        assert f"Thread(target={job}" not in src


def test_concurrent_web_requests_do_not_interleave_users(mu):
    seen = {}
    barrier = threading.Barrier(2)

    def worker(slug):
        with userctx.as_user(slug):
            barrier.wait(5)
            seen[slug] = userctx.current()

    ts = [threading.Thread(target=worker, args=(s,)) for s in ("alex", "maria")]
    [t.start() for t in ts]; [t.join(5) for t in ts]
    assert seen == {"alex": "alex", "maria": "maria"}


# --- web identity ------------------------------------------------------------

class _H(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def test_request_user_trusts_header_only_from_loopback(zeev):
    assert zeev._request_user(_H({"X-Zeev-User": "maria"}), "127.0.0.1") == ("maria", None)
    # A LAN peer cannot claim to be someone by sending the header itself.
    slug, err = zeev._request_user(_H({"X-Zeev-User": "maria"}), "10.0.0.5")
    assert slug == "alex" and err is None


def test_unknown_login_is_rejected_not_defaulted_to_alex(zeev):
    slug, err = zeev._request_user(_H({"X-Zeev-User": "stranger"}), "127.0.0.1")
    assert slug is None and err


def test_proxied_request_without_identity_is_rejected(zeev):
    slug, err = zeev._request_user(_H({"X-Forwarded-For": "1.2.3.4"}), "127.0.0.1")
    assert slug is None and err


def test_direct_lan_request_is_the_device_owner(zeev):
    assert zeev._request_user(_H({}), "10.0.0.7") == ("alex", None)


def test_user_slug_cannot_traverse_paths(zeev):
    for bad in ("../alex", "maria/../../x", "MARIA;", ""):
        assert userctx.resolve(bad) in (None, "maria")
    assert userctx.resolve("../alex") is None
    with pytest.raises(KeyError):
        zeev._udb("../evil")


def test_web_session_is_per_user(mu):
    session = mu._UserList("session", lambda: [])
    with userctx.as_user("alex"):
        session.append({"role": "user", "content": "alex turn"})
    with userctx.as_user("maria"):
        assert len(session) == 0
        session.append({"role": "user", "content": "maria turn"})
        session[:] = session[-60:]
    with userctx.as_user("alex"):
        assert [m["content"] for m in session] == ["alex turn"]


def test_agent_workspace_is_a_sibling_folder_not_alexs(zeev, monkeypatch, tmp_path):
    import agent_fs
    monkeypatch.setenv("ZEEV_AGENT_ROOT", str(tmp_path / "zeev-workspace"))
    with userctx.as_user("alex"):
        assert agent_fs.agent_root() == tmp_path / "zeev-workspace"
    with userctx.as_user("maria"):
        root = agent_fs.agent_root()
    assert root == tmp_path / "zeev-workspace-maria"
    assert not root.is_relative_to(tmp_path / "zeev-workspace")


# --- device sign-in ----------------------------------------------------------

@pytest.mark.parametrize("text,who", [
    ("this is Maria", "maria"),
    ("Hey Zeev, this is Maria", "maria"),
    ("Maria here", "maria"),
    ("it's Maria", "maria"),
    ("switch to Maria", "maria"),
    ("this is Maria and remind me to sing", "maria"),
    ("this is Maria, what's the weather", "maria"),
    ("this is Alex", "alex"),
    ("back to Alex", "alex"),
])
def test_device_user_intent_positive(zeev, text, who):
    assert zeev.device_user_intent(text) == who


@pytest.mark.parametrize("text", [
    "it's Maria's birthday tomorrow",
    "call my wife Maria",
    "tell Maria dinner is ready",
    "what's the weather",
    "is this Maria's phone number",
    "I'm going to the store with Maria",
    "its Maria birthday tomorrow",          # Whisper drops the apostrophe
    "this is Maria birthday party stuff",
    "Maria here is a good idea",
])
def test_device_user_intent_negative(zeev, text):
    """Talking ABOUT Maria must never sign her in (the loop documented in
    detect_active_speaker: Alex mentions her, memory flips)."""
    assert zeev.device_user_intent(text) is None


@pytest.fixture
def dctx(mu, monkeypatch):
    monkeypatch.setattr(mu, "_audio", None)
    c = type("C", (mu._DeviceCtx,), {})()
    c.session, c.spoke = [], []
    c.board = types.SimpleNamespace(set_rgb=lambda *a: None)
    c._set_face = lambda *a, **k: None
    c._go_idle = c._go_ready = lambda: None
    c._speak_device = lambda text, voice="sarina": c.spoke.append(text)
    c._progressive_speak = c._speak_device
    c._stream_speak = lambda *a, **k: ""
    c._busy, c._speak_cancel = threading.Event(), threading.Event()
    c._pending_detail, c._pending_detail_source = [None], [None]
    c._pending_detail_ready = threading.Event()
    c._turn_count, c._voice_coach_pending = [0], [False]
    c._visual_effect_active = [False]
    c._LED_ERROR = c._LED_SPEAKING = c._LED_THINKING = (0, 0, 0)
    c._MORE_YES_RE = re.compile(r"\byes\b")
    c._have_pil = False
    c._followup_listen = lambda: ""
    return c


def test_device_signin_greets_by_name_and_files_history_under_maria(mu, dctx, tmp_path):
    mu.handle_transcript(dctx, "this is Maria")
    assert any("Hi Maria" in s for s in dctx.spoke), "a misheard switch must be audible"
    assert mu._DEVICE_USER[0] == "maria"
    maria = [r[2] for r in _rows(tmp_path / "users" / "maria" / "zeev.db", "messages")]
    assert any("this is Maria" in m for m in maria)
    assert not (tmp_path / "zeev.db").exists() or not any(
        "this is Maria" in r[2] for r in _rows(tmp_path / "zeev.db", "messages"))


def test_device_session_is_swapped_so_alexs_turns_are_not_marias_context(mu, dctx):
    dctx.session = [{"role": "user", "content": "alex private chat"}]
    mu.handle_transcript(dctx, "this is Maria")
    assert all("alex private chat" not in m["content"] for m in dctx.session)
    mu.handle_transcript(dctx, "back to Alex")
    assert any("alex private chat" in m["content"] for m in dctx.session)


def test_device_hands_back_to_alex_after_idle(mu, dctx, monkeypatch):
    mu.handle_transcript(dctx, "this is Maria")
    assert mu._DEVICE_USER[0] == "maria"
    monkeypatch.setattr(mu, "_DEVICE_USER_LAST", [1.0])       # long ago
    monkeypatch.setattr(mu, "_handle_transcript", lambda *a, **k: None)
    mu.handle_transcript(dctx, "what's the weather")
    assert mu._DEVICE_USER[0] == "alex"


def test_device_turn_runs_as_the_signed_in_user(mu, dctx, monkeypatch):
    seen = []
    monkeypatch.setattr(mu, "_handle_transcript",
                        lambda c, t, d=0: seen.append(userctx.current()))
    mu.handle_transcript(dctx, "this is Maria and remind me to sing")   # not a bare intro
    mu.handle_transcript(dctx, "what time is it")
    assert seen == ["maria", "maria"]
    assert userctx.current() == "alex", "context must be restored after the turn"


def test_reminder_set_by_maria_still_fires_after_alex_takes_over(mu):
    with userctx.as_user("maria"):
        mu.add_reminder_at("Maria's pills", 1.0)
    assert "maria" in mu._active_user_slugs()
    fired = []
    for slug in mu._active_user_slugs():
        with userctx.as_user(slug):
            fired += [r["text"] for r in mu.due_reminders(now=10.0)]
    assert fired == ["Maria's pills"]


def test_users_who_never_spoke_get_no_database_file(mu, tmp_path):
    assert mu._active_user_slugs() == ["alex"]
    assert not (tmp_path / "users").exists()


def test_public_pwa_files_need_no_identity_but_the_app_does(zeev):
    """nginx serves the manifest/icons without the login check (Chrome fetches
    them during install), so they arrive proxied with no user header."""
    proxied = _H({"X-Forwarded-For": "1.2.3.4"})
    assert zeev._request_user(proxied, "127.0.0.1", "/manifest.webmanifest") == ("alex", None)
    assert zeev._request_user(proxied, "127.0.0.1", "/icons/icon-192.png") == ("alex", None)
    slug, err = zeev._request_user(proxied, "127.0.0.1", "/chat")
    assert slug is None and err
    slug, err = zeev._request_user(proxied, "127.0.0.1", "/memory")
    assert slug is None and err


def test_first_note_by_a_user_is_not_listed_twice(mu):
    """Found live: the per-user cache loads lazily, so the first add_note after
    a restart loaded the row it had just inserted, then appended it again."""
    with userctx.as_user("maria"):
        notes = mu.add_note("only once")
        assert [n["text"] for n in notes] == ["only once"]
        assert [n["text"] for n in mu.load_notes()] == ["only once"]
