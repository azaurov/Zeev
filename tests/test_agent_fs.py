"""Sandbox invariants for zeev/agent_fs.py -- the model must never read outside
the workspace or reach secrets, whatever path string it invents."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "zeev"))
import agent_fs  # noqa: E402


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "notes.txt").write_text("alpha beta\nthe secret plan is tacos\n")
    (root / "sub").mkdir()
    (root / "sub" / "deep.txt").write_text("needle in a haystack\n")
    (root / ".env").write_text("GROQ_API_KEY=gsk_LEAKME\n")
    (root / "zeev.db").write_bytes(b"SQLite format 3\x00LEAKME")
    (root / "my_api_key.txt").write_text("LEAKME\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-LEAKME\n")
    (root / "link.txt").symlink_to(outside)
    (root / "linkdir").symlink_to(tmp_path)
    monkeypatch.setenv("ZEEV_AGENT_ROOT", str(root))
    return root


@pytest.mark.parametrize("p", [
    "../outside.txt", "../../etc/passwd", "/etc/passwd", "sub/../../outside.txt",
    "link.txt", "linkdir/outside.txt", "linkdir/ws/notes.txt/../../outside.txt",
])
def test_read_cannot_leave_workspace(ws, p):
    out = agent_fs.read_file(p)
    assert out.startswith("Error:"), out
    assert "LEAKME" not in out and "root:" not in out


@pytest.mark.parametrize("p", [".env", "zeev.db", "my_api_key.txt", "./.env", "sub/../.env"])
def test_denied_names_unreadable_inside_root(ws, p):
    out = agent_fs.read_file(p)
    assert out.startswith("Error:"), out
    assert "LEAKME" not in out


def test_listing_hides_denied_and_escaping_entries(ws):
    out = agent_fs.list_dir(".")
    assert "notes.txt" in out and "sub/" in out
    for hidden in (".env", "zeev.db", "my_api_key", "link.txt", "linkdir"):
        assert hidden not in out


def test_list_outside_refused(ws):
    assert agent_fs.list_dir("..").startswith("Error:")
    assert agent_fs.list_dir("linkdir").startswith("Error:")


def test_search_never_returns_denied_or_outside_content(ws):
    out = agent_fs.search_files("LEAKME")
    assert out == "No matches."
    out = agent_fs.search_files("needle")
    assert "sub/deep.txt" in out


def test_search_finds_filename_and_content(ws):
    assert "notes.txt" in agent_fs.search_files("tacos")
    assert "filename match" in agent_fs.search_files("notes")


def test_read_happy_path_is_labelled_untrusted(ws):
    out = agent_fs.read_file("notes.txt")
    assert "tacos" in out and "untrusted data" in out


def test_read_truncates_and_offset_continues(ws):
    (ws / "big.txt").write_text("x" * (agent_fs.MAX_READ_CHARS + 500))
    first = agent_fs.read_file("big.txt")
    assert "truncated" in first and f"offset={agent_fs.MAX_READ_CHARS}" in first
    rest = agent_fs.read_file("big.txt", offset=agent_fs.MAX_READ_CHARS)
    assert "truncated" not in rest and "x" * 500 in rest


def test_binary_refused(ws):
    (ws / "b.bin").write_bytes(b"\x00\x01\x02binary")
    assert agent_fs.read_file("b.bin").startswith("Error:")


def test_missing_workspace_is_an_error_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("ZEEV_AGENT_ROOT", str(tmp_path / "nope"))
    assert agent_fs.list_dir(".").startswith("Error:")


def test_bad_arguments_do_not_raise(ws):
    assert agent_fs.run_agent_tool("read_file", {"path": None}).startswith("Error:")
    assert agent_fs.run_agent_tool("read_file", {"path": "notes.txt", "offset": "abc"})
    assert agent_fs.run_agent_tool("search_files", {"query": ""}).startswith("Error:")
    assert agent_fs.run_agent_tool("rm_rf", {}).startswith("Error:")
    assert agent_fs.run_agent_tool("list_dir", "not a dict")   # coerced, lists root


def test_tools_are_read_only():
    names = agent_fs.AGENT_TOOL_NAMES
    assert names == {"list_dir", "read_file", "search_files"}
    src = Path(agent_fs.__file__).read_text()
    for banned in ("open(target, \"w", "write_text", "unlink", "os.remove", "shutil", "subprocess"):
        assert banned not in src
