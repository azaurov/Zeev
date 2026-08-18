# Add a periodic Zeev job (systemd timer)

Wire a standalone Zeev script (e.g. `zeev/foo.py`) up to run on a schedule on
`ragnar@ragnarok`. This project moved off literal crontab entries in favor of
systemd timers — the reason: if the Pi is asleep/off at the scheduled time,
plain cron simply loses that run, while a timer with `Persistent=true` fires
it on the next boot instead. Existing examples to pattern-match against:
`zeev-quantum-daily`, `zeev-weekly-reflection`, `zeev-rag-probe`.

## When to use this

The user (or a task) wants some Zeev script to run repeatedly on its own,
unattended — a digest, a probe, a maintenance sweep — not something triggered
by a live user turn.

## Step 1 — Confirm the script is standalone-safe

The script must be runnable as `python3 zeev/<script>.py` with no arguments
needed for a normal run, load its own `.env` (see `weekly_reflection.py` or
`news_digest.py` for the copy-paste `.env` loader block), and exit non-zero
on real failure (systemd/journalctl use the exit code and stderr).

## Step 2 — Write the service unit

`/etc/systemd/system/zeev-<name>.service`:

```ini
[Unit]
Description=Zeev <one-line description>
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ragnar
WorkingDirectory=/home/ragnar/Zeev
ExecStart=/usr/bin/python3 zeev/<script>.py
StandardOutput=append:/home/ragnar/Zeev/zeev/data/<name>.log
StandardError=append:/home/ragnar/Zeev/zeev/data/<name>.log
TimeoutStartSec=<generous ceiling — see note below>
```

`TimeoutStartSec` needs real headroom, not a guess: bosgame routinely times
out on a heavy prompt and falls through to Groq, so budget for *both*
attempts plus network slop, not just the happy path. `weekly_reflection`
uses 1800s for exactly this reason; a short single-LLM-call script can use
much less (`quantum_daily` uses 900s).

## Step 3 — Write the timer unit

`/etc/systemd/system/zeev-<name>.timer`:

```ini
[Unit]
Description=Zeev <one-line description> (<human-readable schedule>)

[Timer]
OnCalendar=<systemd calendar spec>
Persistent=true
RandomizedDelaySec=<120-300>
Unit=zeev-<name>.service

[Install]
WantedBy=timers.target
```

**Stagger the schedule** — check `systemctl list-timers --all | grep zeev`
first and pick a time that doesn't collide with the existing cluster
(quantum-daily 06:00, rag-probe 06:30, weekly-reflection Sun 07:00). A few
minutes of `RandomizedDelaySec` on top absorbs the case where two jobs still
land close together and would otherwise compete for bosgame/bandwidth at
the exact same second.

`OnCalendar` examples: `*-*-* 06:00:00` (daily), `Sun *-*-* 07:00:00`
(weekly), `*-*-* 00,06,12,18:15:00` (every 6h, staggered off the hour).

## Step 4 — Deploy the units

Both files need root, so write them via `sudo tee`:

```bash
ssh ragnar@ragnarok "sudo tee /etc/systemd/system/zeev-<name>.service > /dev/null" <<'EOF'
...
EOF
ssh ragnar@ragnarok "sudo tee /etc/systemd/system/zeev-<name>.timer > /dev/null" <<'EOF'
...
EOF
ssh ragnar@ragnarok "sudo systemctl daemon-reload && sudo systemctl enable --now zeev-<name>.timer"
```

`ragnar` has passwordless sudo on ragnarok (same as the deploy script's own
`sudo systemctl restart zeev-device`), so these run non-interactively.

## Step 5 — Verify

```bash
ssh ragnar@ragnarok "systemctl list-timers zeev-<name>.timer --all"
```

Confirms the timer is enabled and shows the next scheduled fire.

**Do a manual trigger to prove the unit itself works**, rather than waiting
for the schedule:

```bash
ssh ragnar@ragnarok "sudo systemctl start zeev-<name>.service"
```

This is a oneshot unit — `systemctl start` blocks until `ExecStart` exits, so
a real script (LLM calls, network I/O) can easily run past a single SSH
command's timeout. Poll instead of assuming a quick `systemctl is-active`
check tells you anything: `is-active` reports `activating` (not `active`)
for the whole time a oneshot's `ExecStart` is still running, so checking for
literal `active` returns immediately and wrongly looks "done". Poll on the
PID instead:

```bash
ssh ragnar@ragnarok "timeout 300 bash -c 'while kill -0 <PID> 2>/dev/null; do sleep 5; done'; systemctl status zeev-<name>.service --no-pager -l | tail -15; tail -60 /home/ragnar/Zeev/zeev/data/<name>.log"
```

Get `<PID>` from the `Main PID:` line of an initial `systemctl status`
right after starting it.

Read the tail of the unit's own log file (not just `systemctl status`) —
that's where the script's actual print output and any stack trace live.

## Step 6 — Report

Tell the user: what the schedule is (in plain terms — "every 6 hours,
staggered off the other jobs"), that the timer is enabled, and the result of
the manual trigger (success, or the actual error if it failed — don't just
say "it's set up" without having proven the unit runs).
