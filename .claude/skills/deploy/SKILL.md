# Deploy Zeev
Commit staged changes (never include data/*.json), push to origin, then ssh to the Pi at ragnar@ragnarok, pull, and run the following in order:

1. `python3 zeev/migrate_to_sqlite.py` — idempotent, safe to re-run; imports any flat-file data (history.jsonl, user_memory.json, notes.jsonl, settings.json) into zeev.db if not already there. No-ops on rows already present.
2. `sudo systemctl restart zeev-device`
3. Tail logs (`journalctl -u zeev-device -n 20 --no-pager`) and verify the startup greeting played and prior turns were loaded.

After a successful deploy:
1. Update `/home/azaurov/Zeev/CLAUDE.md` to reflect any architectural changes made in this session (new functions, changed behaviour, updated constants, new endpoints, etc.). Keep it accurate and concise — do not pad, do not duplicate what's already there.
2. Update `/home/azaurov/Zeev/README.md` to reflect the same changes for a public audience — what Zeev is, how to run it, key features, and any new capabilities added this session. Commit both files together in a single `docs:` commit and push.
