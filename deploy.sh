#!/bin/bash
set -e

PI_LAN="ragnar@ragnarok"
PI_TS="ragnar@ragnarok.tail9c2c7c.ts.net"
SERVICE="zeev-device"

# Seconds to wait for the device to come up healthy before rolling back.
# Cold start on a Pi Zero 2W: ~5s to the banner, ~25s once the wake model loads.
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-75}"

# Log line that means "really up". Overridable so the rollback path itself can
# be tested against a marker that will never appear.
HEALTH_MARKER="${HEALTH_MARKER:-Zeev Device Mode}"

# Resolve which SSH target is reachable
PI=""
if ssh -o ConnectTimeout=5 -o BatchMode=yes "$PI_LAN" true 2>/dev/null; then
    PI="$PI_LAN"
elif ssh -o ConnectTimeout=10 -o BatchMode=yes "$PI_TS" true 2>/dev/null; then
    PI="$PI_TS"
else
    echo "ERROR: Pi unreachable on LAN ($PI_LAN) and Tailscale ($PI_TS)." >&2
    exit 1
fi
echo "Connected via $PI"

echo "Pushing to origin..."
git push origin main

# Remember what the Pi was running so a bad deploy can be undone on the device.
# Deliberately does NOT touch origin/main: an unattended deploy that fails at
# 3am should restore the device now and leave the history for a human to judge.
PREV_SHA="$(ssh "$PI" "cd ~/Zeev && git rev-parse HEAD")"
echo "Pi currently at $PREV_SHA"

echo "Deploying to Pi..."
ssh "$PI" "
  set -e
  cd ~/Zeev
  git pull
  python3 zeev/migrate_to_sqlite.py
  sudo systemctl restart $SERVICE
"

# ── Health gate ─────────────────────────────────────────────────────────────
# 'active' alone is not health: systemd reports active the moment the process
# execs, well before the device has a display, audio or a wake listener. Wait
# for the startup banner the app only prints once it is genuinely up.
echo "Waiting up to ${HEALTH_TIMEOUT}s for the device to come up..."
HEALTHY=0
# Polled inside a single SSH session -- one connection per second for 75s is
# both slow and a real load on a Pi Zero 2W that is busy starting up.
if ssh "$PI" "
  for _ in \$(seq 1 $HEALTH_TIMEOUT); do
      sleep 1
      if [ \"\$(systemctl is-active $SERVICE)\" = failed ]; then
          echo 'HEALTH: service entered failed state'
          exit 1
      fi
      if journalctl -u $SERVICE --since '-3 min' --no-pager | grep -q \"$HEALTH_MARKER\"; then
          # Up -- but a crashed background thread is still a bad deploy.
          if journalctl -u $SERVICE --since '-3 min' --no-pager \
             | grep -q 'Traceback (most recent call last)'; then
              echo 'HEALTH: startup banner seen, but a traceback was logged'
              exit 1
          fi
          exit 0
      fi
  done
  echo 'HEALTH: timed out waiting for startup banner'
  exit 1
"; then
    HEALTHY=1
fi

if [ "$HEALTHY" = "1" ]; then
    echo
    echo "Deploy healthy."
    ssh "$PI" "journalctl -u $SERVICE --since '-3 min' --no-pager | tail -20"
    exit 0
fi

# ── Rollback ────────────────────────────────────────────────────────────────
echo
echo "!! DEPLOY UNHEALTHY — rolling the Pi back to $PREV_SHA" >&2
ssh "$PI" "journalctl -u $SERVICE --since '-3 min' --no-pager | tail -40" >&2 || true

ssh "$PI" "
  set -e
  cd ~/Zeev
  git reset --hard $PREV_SHA
  sudo systemctl restart $SERVICE
" || { echo "!! ROLLBACK FAILED — device needs manual attention" >&2; exit 2; }

sleep 20
if ssh "$PI" "journalctl -u $SERVICE --since '-1 min' --no-pager \
              | grep -q \"$HEALTH_MARKER\"" 2>/dev/null; then
    echo "Rolled back; device is healthy on $PREV_SHA." >&2
    echo "origin/main still has the bad commit — fix forward, then redeploy." >&2
    exit 1
fi

echo "!! ROLLBACK DID NOT RECOVER — device needs manual attention" >&2
exit 2
