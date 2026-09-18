#!/usr/bin/env bash
# Smoke: a timer's CONFIGURED interval still matches what we think it is, and
# it has actually fired recently.
#
# Two real incidents, neither of which raised anything:
#   * The M400B keepalive ping ran every 2 minutes for weeks while its service
#     was a silent no-op -- a missing XDG_RUNTIME_DIR meant every tick logged
#     "SKIPPED: sink not present" even though the link was fine (2026-09-10).
#     Nothing was failed, nothing alerted; the timer was green the whole time.
#   * That interval was then widened 2min -> 8min for battery reasons
#     (2026-09-11). A drift back the other way is invisible unless asserted.
#
# So this checks the configured interval AND recency: a timer that is armed but
# whose service has not run is the failure mode that looks healthy.
#
# Exits 0 on pass, 1 on failure, 77 (SKIP) when not on the host that owns these
# units -- a skip must never read as a pass, so it gets its own code.
set -uo pipefail

# NB: capture first, match second. `systemctl ... | grep -q` under
# `set -o pipefail` reports 141: grep -q exits at the first match, systemctl
# dies of SIGPIPE, and pipefail surfaces that as a pipeline failure -- so the
# guard "failed" and this script skipped on the very host that owns the units.
# A skip that silently covers nothing is exactly the bug class this directory
# exists to catch (found while writing it, 2026-09-18).
_units=$(systemctl list-unit-files 2>/dev/null || true)
case "$_units" in
    *yard-speaker-ping.timer*) ;;
    *) echo "SKIP: yard-speaker units are not on this host"; exit 77;;
esac

fail=0

# unit:expected_seconds
for spec in yard-speaker-ping:480 yard-speaker-battery-log:1800 dog-detector-status:600; do
    unit="${spec%%:*}"; want="${spec##*:}"
    conf=$(systemctl cat "$unit.timer" 2>/dev/null | sed -n 's/^OnUnitActiveSec=//p' | head -1)
    case "$conf" in
        *min) got=$(( ${conf%min} * 60 ));;
        *s)   got=${conf%s};;
        *)    got="";;
    esac
    if [ -z "$got" ]; then
        echo "FAIL: $unit.timer has no parseable OnUnitActiveSec (got '$conf')"; fail=1; continue
    fi
    if [ "$got" != "$want" ]; then
        echo "FAIL: $unit.timer interval drifted: configured ${got}s, expected ${want}s"
        fail=1
    else
        echo "ok: $unit.timer every ${got}s"
    fi

    # Recency: measured from the SERVICE's last run, not the timer's last
    # trigger. Restarting a stale timer does not change LastTriggerUSec, so a
    # timer-based reading reports a healed timer as stale forever.
    last=$(systemctl show -p InactiveExitTimestamp --value "$unit.service" 2>/dev/null)
    [ -z "$last" ] || [ "$last" = "n/a" ] && \
        last=$(systemctl show -p LastTriggerUSec --value "$unit.timer" 2>/dev/null)
    if [ -z "$last" ] || [ "$last" = "n/a" ]; then
        echo "FAIL: $unit has never run"; fail=1; continue
    fi
    age=$(( $(date +%s) - $(date -d "$last" +%s 2>/dev/null || echo 0) ))
    # Two intervals of slack: one missed tick is jitter, two is drift.
    if [ "$age" -gt $(( want * 2 )) ]; then
        echo "FAIL: $unit last ran ${age}s ago, expected every ${want}s"; fail=1
    else
        echo "ok: $unit ran ${age}s ago"
    fi
done

exit $fail
