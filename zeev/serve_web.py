#!/usr/bin/env python3
"""Production launcher for Zeev's web chat mode on this box (sogdiana-gematria.net).

Run under systemd as zeev-web.service. `python3 zeev/zeev.py --web` can't be
used directly here because it hardcodes port 5000, which this same box's
main PHP site already binds -- so, like .claude/skills/run-zeev/driver.py,
this imports zeev.py as a module (safe: every side effect in zeev.py is
gated behind `if __name__ == "__main__":`) and calls run_web_server()
with the port nginx's zeev.sogdiana-gematria.net vhost actually proxies to.

Runs in the foreground on purpose -- systemd's Type=simple expects the
main process to just stay up; Restart=on-failure in the unit handles a
real crash.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import zeev  # noqa: E402  (import after sys.path patch)

if __name__ == "__main__":
    # host pinned to loopback so nginx (TLS + AOP + Basic auth) is the only
    # path in -- the old 0.0.0.0 default left the plaintext, unauthenticated
    # backend reachable on every interface, protected only by UFW having no
    # explicit allow rule for this port.
    zeev.run_web_server(host="127.0.0.1", port=5057)
