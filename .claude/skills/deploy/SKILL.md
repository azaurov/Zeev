# Deploy Zeev
Stage the changes (never include data/*.json), then run `./deploy.sh "commit message"` — this is the only sanctioned deploy path (see CLAUDE.md "Deploy & Verify Loop"). It runs the full test suite, commits, pushes, pulls on the Pi, runs the SQLite migration, hard-asserts the Pi's HEAD matches local before restarting `zeev-device`, polls for the real startup banner in `journalctl` (not just `systemctl is-active`), and auto-rolls back the Pi on an unhealthy deploy. Do not SSH in and restart the service manually — it skips all of these gates.

Report PASS/FAIL explicitly based on the script's own output: it never claims success without printing "Deploy healthy." and a `journalctl -n 50` tail. Paste the relevant log lines directly in chat rather than leaving them only in shell output.

After a successful deploy:
1. Update `/home/azaurov/Zeev/CLAUDE.md` to reflect any architectural changes made in this session (new functions, changed behaviour, updated constants, new endpoints, etc.).
   - **Hard limit: CLAUDE.md must stay under 120 000 characters.** Check with `wc -c` after editing. (Raised 2026-08-04 from a stale 25 000 — the file was already at ~100 000 from accumulated incident write-ups, so the old number flagged on every deploy regardless of what changed.)
   - If adding new content would exceed the limit, trim or compress existing sections first — remove obvious/redundant detail, shorten verbose descriptions, compress bullet lists. Never just append.
   - Only document non-obvious constraints, gotchas, and decision rationale. Omit anything obvious from reading the code or function names.
2. Update `/home/azaurov/Zeev/README.md` to reflect the same changes for a public audience — what Zeev is, how to run it, key features, and any new capabilities added this session. Commit both files together in a single `docs:` commit and push.
3. Update the **Zeev** section in the GitHub profile README (`github.com/azaurov`):
   - If `/home/azaurov/azaurov/README.md` does not exist, clone it first: `git clone git@github.com:azaurov/azaurov.git /home/azaurov/azaurov`
   - Edit the Zeev section to reflect the same new capabilities just described in `/home/azaurov/Zeev/README.md` (keep it brief — 2–4 bullet points max).
   - Commit with `docs: update Zeev section` and push.
