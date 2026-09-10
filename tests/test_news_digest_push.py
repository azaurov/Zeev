"""news_digest.py's _push_to_admin() -- best-effort LAN-then-Tailscale push
of a fresh digest to bosgame's admin panel. Must never raise, and must stop
trying hosts as soon as one succeeds.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "zeev"))
import news_digest  # noqa: E402


def _completed(returncode):
    r = MagicMock()
    r.returncode = returncode
    return r


def test_push_stops_after_lan_success():
    with patch("news_digest.subprocess.run", return_value=_completed(0)) as run:
        news_digest._push_to_admin("some digest text", 1234567890.0)
    assert run.call_count == 1
    assert news_digest._ADMIN_PUSH_HOSTS[0] in run.call_args[0][0]


def test_push_falls_back_to_tailscale_on_lan_failure():
    with patch(
        "news_digest.subprocess.run",
        side_effect=[_completed(1), _completed(0)],
    ) as run:
        news_digest._push_to_admin("some digest text", 1234567890.0)
    assert run.call_count == 2
    assert news_digest._ADMIN_PUSH_HOSTS[1] in run.call_args[0][0]


def test_push_swallows_total_failure(capsys):
    with patch("news_digest.subprocess.run", side_effect=Exception("unreachable")):
        news_digest._push_to_admin("some digest text", 1234567890.0)
    assert "could not push digest" in capsys.readouterr().err


def test_push_payload_contains_content_and_ts():
    with patch("news_digest.subprocess.run", return_value=_completed(0)) as run:
        news_digest._push_to_admin("hello world", 42.0)
    sent = run.call_args.kwargs["input"]
    assert '"content": "hello world"' in sent
    assert '"ts": 42.0' in sent
