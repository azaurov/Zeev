#!/usr/bin/env bash
# Smoke: when a Bluetooth audio device reports Connected, an actual audio PCM
# exists for it.
#
# "Connected" alone is not enough and has misled this project more than once: a
# device can hold an ACL link with no A2DP transport, and the BlueALSA PCM
# listing is the thing that decides whether audio can actually play. Separately,
# a wrong-profile state (headset-head-unit instead of a2dp-sink) presents as
# connected while sounding like a telephone or not playing at all.
#
# Read-only by construction: it never connects, scans or disconnects. The
# yard-speaker watcher already owns reconnection and holds the relevant lock;
# a second reconnect path just collides with it.
#
# Exits 0 pass, 1 fail, 77 skip (device not connected / tools absent).
set -uo pipefail

MAC="${SMOKE_BT_MAC:-F4:4E:FD:86:8E:F8}"   # M400B yard speaker
command -v bluetoothctl >/dev/null 2>&1 || { echo "SKIP: no bluetoothctl"; exit 77; }

info=$(bluetoothctl info "$MAC" 2>/dev/null)
if ! echo "$info" | grep -q "Connected: yes"; then
    # Not a failure: this speaker powers itself off by design and a human turns
    # it back on. Reporting that as a broken test would train everyone to
    # ignore this script.
    echo "SKIP: $MAC is not connected (powered off / out of range)"
    exit 77
fi

fail=0
sink=$(XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}" pactl list sinks short 2>/dev/null \
       | grep -c "bluez_output.$MAC" || true)
if [ "${sink:-0}" -lt 1 ]; then
    echo "FAIL: $MAC reports Connected but has no PipeWire sink -- audio cannot play"
    fail=1
else
    echo "ok: $MAC connected with an audio sink present"
fi

prof=$(XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}" pactl list cards 2>/dev/null \
       | grep -A2 "bluez_card.${MAC//:/_}" | grep -o "a2dp[-_a-z]*" | head -1 || true)
if [ -n "$prof" ]; then
    echo "ok: profile $prof"
else
    echo "WARN: could not read the active profile (not failing on this alone)"
fi
exit $fail
