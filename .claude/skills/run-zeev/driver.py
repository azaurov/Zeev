#!/usr/bin/env python3
"""Driver for exercising Zeev's web mode without --web's hardcoded port 5000.

Port 5000 is often already taken on this box by an unrelated production
Flask app (the sogdiana-gematria.net site), so this imports zeev.py as a
module (safe -- everything in the file is gated behind
`if __name__ == "__main__":`) and calls run_web_server() directly with a
free port, instead of shelling out to `python3 zeev/zeev.py --web`.

Usage:
    python3 .claude/skills/run-zeev/driver.py [--port 5057]

Then drive it with curl against http://127.0.0.1:<port>/ -- see SKILL.md.
Ctrl-C to stop.
"""
import argparse
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "zeev"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5057)
    args = ap.parse_args()

    import zeev  # noqa: E402  (import after sys.path patch)

    t = threading.Thread(
        target=zeev.run_web_server, kwargs={"port": args.port}, daemon=True
    )
    t.start()
    time.sleep(1.5)
    print(f"READY on http://127.0.0.1:{args.port}/", flush=True)
    try:
        while t.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
