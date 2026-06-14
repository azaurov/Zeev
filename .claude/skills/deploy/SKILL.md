# Deploy Zeev
Commit staged changes (never include data/*.json), push to origin, then ssh to the Pi at ragnar@ragnarok, pull, restart the zeev-device systemd service, and tail logs to verify startup greeting played.

After a successful deploy, update /home/azaurov/Zeev/CLAUDE.md to reflect any architectural changes made in this session (new functions, changed behaviour, updated constants, new endpoints, etc.). Keep it accurate and concise — do not pad, do not duplicate what's already there.
