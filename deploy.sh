#!/bin/bash
set -e

PI_LAN="ragnar@ragnarok"
PI_TS="ragnar@ragnarok.tail9c2c7c.ts.net"
SERVICE="zeev-device"

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

echo "Deploying to Pi..."
ssh -t "$PI" "
  set -e
  cd ~/Zeev
  git pull
  python3 zeev/migrate_to_sqlite.py
  sudo systemctl restart $SERVICE
  sleep 5
  journalctl -u $SERVICE -n 30 --no-pager
"
