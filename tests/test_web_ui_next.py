"""The noir-splash redesign (zeev/web_ui_next.html, served at /next) must not lose
anything the live page (_WEB_HTML) can do. A restyle fails silently: a renamed
id makes a button dead with no error anywhere, so parity is pinned here."""
import re
from pathlib import Path

import pytest

NEXT = Path(__file__).parent.parent / "zeev" / "web_ui_next.html"


@pytest.fixture(scope="module")
def nxt():
    return NEXT.read_text(encoding="utf-8")


def _ids(html):
    return set(re.findall(r'\bid="([^"]+)"', html))


def _js_ids(html):
    return set(re.findall(r'getElementById\("([^"]+)"\)', html))


def _fetches(html):
    return set(re.findall(r'fetch\("(/[a-z\-]+)"', html))


def test_every_live_element_id_survives(zeev, nxt):
    missing = _ids(zeev._WEB_HTML) - _ids(nxt)
    assert not missing, f"redesign dropped element ids: {sorted(missing)}"


def test_every_id_the_js_looks_up_exists(nxt):
    missing = _js_ids(nxt) - _ids(nxt)
    assert not missing, f"getElementById targets with no element: {sorted(missing)}"


def test_every_live_endpoint_is_still_called(zeev, nxt):
    assert _fetches(zeev._WEB_HTML) <= _fetches(nxt)


def test_pending_placeholder_is_css_not_a_stray_text_node(nxt):
    # The live page sets textContent = "⋯" and then appends the reply span after it,
    # so every reply rendered as "⋯answer". Dots must come from ::before instead.
    assert 'text || "⋯"' not in nxt
    assert ".zeev-bubble.pending::before" in nxt


def test_splash_is_dismissable_and_storage_safe(nxt):
    assert 'id="splash"' in nxt and 'id="enterBtn"' in nxt
    assert "nosplash" in nxt
    # sessionStorage can throw (private windows, blocked storage): every use is guarded
    for m in re.finditer(r"sessionStorage\.\w+Item", nxt):
        window = nxt[max(0, m.start() - 60):m.start()]
        assert "try {" in window, f"unguarded sessionStorage use near: {window!r}"


def test_reduced_motion_is_respected(nxt):
    assert "prefers-reduced-motion" in nxt


def test_preview_route_exists_and_live_route_is_untouched(zeev):
    src = Path(zeev.__file__).read_text()
    assert '== "/next"' in src and "web_ui_next.html" in src
    assert 'if self.path in ("/", "/index.html"):\n                body = _WEB_HTML.encode' in src
