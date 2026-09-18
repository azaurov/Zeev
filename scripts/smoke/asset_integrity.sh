#!/usr/bin/env bash
# Smoke: generator scripts have not overwritten a real asset.
#
# A generator that writes its output to a path a real asset already occupies
# destroys it silently -- the script succeeds, the file exists, and only a human
# looking at the image ever notices. Checksums turn that into a test.
#
# Usage: keep a manifest of "path  sha256" for assets that are real content
# (photographed, drawn, curated) rather than generated. Anything generated
# belongs OUT of the manifest -- its checksum changes legitimately.
#
# Exits 0 pass, 1 fail, 77 skip (no manifest on this host).
set -uo pipefail

HERE="$(dirname "$(readlink -f "$0")")"
MANIFEST="${ASSET_MANIFEST:-$HERE/asset_manifest.txt}"

[ -f "$MANIFEST" ] || { echo "SKIP: no asset manifest at $MANIFEST"; exit 77; }

fail=0
checked=0
while read -r path want; do
    case "$path" in ''|'#'*) continue;; esac
    if [ ! -f "$path" ]; then
        echo "FAIL: missing asset $path"; fail=1; continue
    fi
    got=$(sha256sum "$path" | cut -d' ' -f1)
    checked=$((checked + 1))
    if [ "$got" != "$want" ]; then
        echo "FAIL: $path changed (a generator may have overwritten it)"
        echo "      expected $want"
        echo "      got      $got"
        fail=1
    fi
done < "$MANIFEST"

[ "$fail" = 0 ] && echo "ok: $checked protected assets unchanged"
exit $fail
