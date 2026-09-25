"""Zeev web is an installable app: manifest + service worker + icons must exist, be
linked from the served page, and be reachable through the handler's allow-list.
Chrome silently refuses to offer Install when any of these is missing or wrong."""
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).parent.parent / "zeev"
PWA = ROOT / "pwa"


def _png_size(p):
    b = p.read_bytes()
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", b[16:24])


def test_manifest_is_installable():
    m = json.loads((PWA / "manifest.webmanifest").read_text())
    assert m["display"] == "standalone" and m["start_url"] and m["name"]
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(i.get("purpose") == "maskable" for i in m["icons"])


def test_every_manifest_icon_is_served_and_real_size(zeev):
    z = zeev
    m = json.loads((PWA / "manifest.webmanifest").read_text())
    for i in m["icons"]:
        assert i["src"] in z._PWA_FILES, i["src"]
        fname = z._PWA_FILES[i["src"]][0]
        w, h = _png_size(PWA / fname)
        assert f"{w}x{h}" == i["sizes"]


def test_service_worker_and_manifest_served(zeev):
    z = zeev
    assert z._PWA_FILES["/sw.js"][0] == "sw.js"
    assert z._PWA_FILES["/manifest.webmanifest"][1] == "application/manifest+json"
    assert (PWA / "sw.js").read_text().count("fetch") >= 1


def test_page_links_manifest_and_registers_sw():
    html = (ROOT / "web_ui.html").read_text(encoding="utf-8")
    # credentials: the whole site is behind a login, an anonymous manifest fetch 302s
    assert re.search(r'rel="manifest"[^>]*crossorigin="use-credentials"', html)
    assert "serviceWorker.register('/sw.js')" in html
    assert 'rel="apple-touch-icon"' in html


def test_app_lock_requires_user_verification_and_only_in_installed_app():
    html = (ROOT / "web_ui.html").read_text(encoding="utf-8")
    assert "display-mode: standalone" in html          # plain browser tabs stay unlocked
    assert html.count("userVerification:'required'") == 2   # enroll AND verify
    assert "authenticatorAttachment:'platform'" in html
    assert 'id="applock"' in html


def test_hidden_lock_overlay_really_hides():
    """The overlay's inline display:flex beats the [hidden] attribute, so without this rule
    the lock never went away (found live: 'Unlock' did nothing, and it showed in plain
    browser tabs too)."""
    html = (ROOT / "web_ui.html").read_text(encoding="utf-8")
    assert re.search(r"#applock\[hidden\]\s*\{\s*display:\s*none\s*!important", html)
