---
name: deploy-verify
description: Deploy a change to an off-repo systemd unit on bosgame or feiergente01, restart it, and prove from logs and a real end-to-end trigger that it works
---

# Deploy & verify an off-repo service

For services **outside** the Zeev repo — the units that `./deploy.sh` never touches:
`yard-speaker-connect` / `-ping` / `-battery-log`, `dogcaller-vm`, `piper-tts`,
`zeev-news-digest`, `zeev-news-probe`, `zeev-rag-probe`, `daily-reboot`, `ZeevTTS`
(feiergente01, NSSM), and anything else living in `/etc/systemd/system` or
`~/troubleshooting/`.

**Not for Zeev itself.** Anything under `/home/azaurov/Zeev` deploying to the Pi
(`zeev-device`, `zeev-audio`, `zeev-watch`) goes through `/deploy` → `./deploy.sh`,
which is the only sanctioned path (CLAUDE.md, "Deploy & Verify Loop"). If the change
is in that repo, stop and use `/deploy` instead — a manual copy-and-restart skips the
test suite, the HEAD-match assertion, the startup-banner health poll and the rollback.

## Steps

1. **Confirm the target.** Which host (`bosgame` | `feiergente01`) and which unit.
   bosgame is **this machine** (10.0.0.141) — run commands locally, no ssh.
   feiergente01 is `ssh azaur@10.0.0.208` (cmd shell; PowerShell hangs — use
   `python`/`sc.exe`). Check the unit exists before editing: `systemctl cat <unit>`.
2. **Check the hardware is actually up first.** A speaker that is powered off or a VM
   with no X session is the more common cause than the code. `bluetoothctl info <mac>`,
   `loginctl list-sessions`, `adb devices`, a ping — whichever applies.
3. **Copy the files**, then `sudo systemctl daemon-reload`.
   Unit files on bosgame want `Environment=XDG_RUNTIME_DIR=/run/user/1000` if the
   script touches PipeWire/`pactl` — it fails silently without it.
4. **Validate before restarting:** `systemd-analyze verify <unit>` — clean output means
   no ordering cycle. `WantedBy=graphical.target` (not `multi-user.target`) for anything
   `After=graphical.target`, or systemd silently drops the start job every boot.
5. **Restart:** `sudo systemctl restart <unit>` (for a `.timer`, restart the timer and
   check `systemctl list-timers --all | grep <unit>` for the next elapse).
6. **Read the real logs:** `journalctl -u <unit> --since '2 minutes ago' --no-pager`.
   Quote the actual lines in chat as evidence — `is-active` reporting `active` is not
   evidence, and a `Type=oneshot` unit sitting `inactive (dead)` is success, not failure.
7. **Trigger the real end-to-end path** and confirm the observed output: play through the
   yard speaker, take a camera snapshot, run the digest by hand, place the call. This step
   is the point of the skill — a clean restart is not a working feature.
8. **Commit only after the logs prove it.** Most of these files live outside any repo:
   run `git rev-parse --is-inside-work-tree` and `git remote -v` first, and say plainly
   when there is nothing to commit to rather than implying it was version-controlled.
9. **On any failure, report it explicitly** with the failing log lines, and revert the
   unit file to its backup. Never infer success from reading the code.
