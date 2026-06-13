#!/bin/bash
set -e

PI="ragnar@ragnarok"
SERVICE="zeev-device"

echo "Pushing to origin..."
git push origin main

echo "Deploying to Pi..."
ssh -t "$PI" "
  cd ~/Zeev &&
  git pull &&
  sudo systemctl restart $SERVICE &&
  sleep 5 &&
  journalctl -u $SERVICE -n 30 --no-pager
"
