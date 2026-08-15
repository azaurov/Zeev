#!/bin/bash
# Deploy Zeev to the Pi. This is the ONLY sanctioned deploy path — see
# CLAUDE.md "Version Control / Deployment". Idempotent: safe to re-run with
# no staged changes (commit/push become no-ops) and re-running after a
# healthy deploy just re-verifies and re-prints the tail.
#
# Usage: ./deploy.sh ["commit message"]
#   commit message is required only if there are staged changes to commit.
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

COMMIT_MSG="$1"

# ── Test gate ────────────────────────────────────────────────────────────────
# 989 tests. Measured live on this box: 770s serial, 279s with `-n auto`
# (pytest-xdist picked 4 workers here, not nproc's 8 -- cgroup/affinity
# limited). `-n auto` is a hard usage error if pytest-xdist isn't installed
# (no graceful fallback in pytest itself), so check for it explicitly rather
# than crash the deploy over a missing optional package.
# TEST_TIMEOUT defaults to 450s -- real headroom over the ~280s xdist run,
# not padding, since one unmocked-network test alone has been observed
# taking 58s by itself. A hang here must fail the deploy, not hide it.
TEST_TIMEOUT="${TEST_TIMEOUT:-450}"
XDIST_ARGS=""
if python3 -c "import xdist" 2>/dev/null; then
    XDIST_ARGS="-n auto"
else
    echo "NOTE: pytest-xdist not installed (sudo apt install python3-pytest-xdist) — running serial, will be slower."
fi
echo "Running test suite (${TEST_TIMEOUT}s timeout)..."
RC=0
# shellcheck disable=SC2086  # intentional word-splitting: XDIST_ARGS is "" or two tokens
timeout "$TEST_TIMEOUT" python3 -m pytest tests/ -x -q --no-header $XDIST_ARGS || RC=$?
if [ "$RC" -eq 124 ]; then
    echo "ERROR: test suite timed out after ${TEST_TIMEOUT}s — a test is hanging. Not deploying." >&2
    exit 1
elif [ "$RC" -ne 0 ]; then
    echo "ERROR: test suite failed. Not deploying." >&2
    exit 1
fi
echo "Tests passed."

# ── Commit + push (idempotent: no staged changes = no-op) ───────────────────
if ! git diff --cached --quiet; then
    if [ -z "$COMMIT_MSG" ]; then
        echo "ERROR: staged changes present but no commit message given. Usage: ./deploy.sh \"message\"" >&2
        exit 1
    fi
    echo "Committing staged changes..."
    git commit -m "$COMMIT_MSG"
else
    echo "No staged changes — skipping commit."
fi

# The test gate above ran against the full working tree, including any
# unstaged edits to tracked files. If those never got staged, they also
# never got committed/pushed above -- deploying HEAD would test one thing
# and ship another, silently. Fail loud instead.
if ! git diff --quiet; then
    echo "ERROR: unstaged changes to tracked files present. They were tested but won't be deployed. Run 'git add' first (or discard them)." >&2
    exit 1
fi

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
if ! git push origin main; then
    echo "ERROR: git push failed. Not deploying." >&2
    exit 1
fi
LOCAL_HEAD="$(git rev-parse HEAD)"

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
"

# ── HEAD-match gate ───────────────────────────────────────────────────────────
# A stale checkout must never be restarted against — that's deploying nothing
# and reporting success. Hard-fail before touching the service.
REMOTE_HEAD="$(ssh "$PI" "cd ~/Zeev && git rev-parse HEAD")"
if [ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]; then
    echo "ERROR: HEAD mismatch after pull — local=$LOCAL_HEAD remote=$REMOTE_HEAD. Not restarting the service." >&2
    exit 1
fi
echo "HEAD verified: Pi matches local ($LOCAL_HEAD)."

ssh "$PI" "sudo systemctl restart $SERVICE"

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
    ssh "$PI" "journalctl -u $SERVICE -n 50 --no-pager"
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
