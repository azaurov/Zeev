"""zeev-audio/scripts/yt-dlp-fast: yt-dlp minus the zipapp startup tax.

The invariant that matters is *staleness*: yt-dlp is updated in place weekly,
and a wrapper that kept running an old extracted copy would silently keep a
broken yt-dlp alive after the update that fixed it (YouTube changes break it
routinely). A wrapper that merely "ran something" would pass every other test.
"""
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

WRAPPER = Path(__file__).parent.parent / "zeev-audio" / "scripts" / "yt-dlp-fast"

pytestmark = pytest.mark.skipif(
    not (shutil.which("flock") and shutil.which("python3")), reason="needs flock")


def _make_zip(path, version):
    """A zipapp shaped like yt-dlp's: shebang prefix, __main__.py at the root."""
    path.write_bytes(b"#!/usr/bin/env python3\n")
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("__main__.py", f"import sys; print('{version}', *sys.argv[1:])\n")
    path.chmod(0o755)


def _run(zip_path, cache, *args):
    env = dict(os.environ, YTDLP_ZIP=str(zip_path), YTDLP_CACHE=str(cache))
    return subprocess.run([str(WRAPPER), *args], env=env, capture_output=True,
                          text=True, timeout=60)


def test_runs_the_extracted_copy_and_passes_args(tmp_path):
    z, cache = tmp_path / "yt-dlp", tmp_path / "cache" / "x"
    _make_zip(z, "v1")
    r = _run(z, cache, "--get-url", "a b")
    assert r.returncode == 0 and r.stdout.strip() == "v1 --get-url a b", r.stderr
    assert (cache / "__main__.py").exists(), "must actually have extracted"


def test_an_updated_zip_is_picked_up_not_served_stale(tmp_path):
    z, cache = tmp_path / "yt-dlp", tmp_path / "cache" / "x"
    _make_zip(z, "v1")
    assert _run(z, cache).stdout.strip() == "v1"
    time.sleep(1.1)                       # mtime has 1s resolution in the stamp
    z.unlink()
    _make_zip(z, "v2")                    # what `yt-dlp -U` does: new file, same path
    assert _run(z, cache).stdout.strip() == "v2"


def test_unchanged_zip_is_not_re_extracted(tmp_path):
    z, cache = tmp_path / "yt-dlp", tmp_path / "cache" / "x"
    _make_zip(z, "v1")
    _run(z, cache)
    marker = cache / "MARK"
    marker.write_text("x")
    _run(z, cache)
    assert marker.exists(), "second call must reuse the cache, not rebuild it"


def test_falls_back_to_the_zip_when_extraction_is_impossible(tmp_path):
    z = tmp_path / "yt-dlp"
    _make_zip(z, "v1")
    blocker = tmp_path / "file"
    blocker.write_text("")                # a file where the cache dir must go
    r = _run(z, blocker / "sub" / "x")
    assert r.returncode == 0 and r.stdout.strip() == "v1", r.stderr
