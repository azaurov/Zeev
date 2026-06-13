# Deploy Zeev
Commit staged changes (never include data/*.json), push to origin, then ssh to the Pi at ragnar@ragnarok, pull, restart the zeev-device systemd service, and tail logs to verify startup greeting played.
