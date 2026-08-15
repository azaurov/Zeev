# Zeev Project Rules

## Deployment Constraints
`./deploy.sh` is the only sanctioned deploy path. Stage your intended local modifications (`git add`) before running it — it commits staged changes itself (`./deploy.sh "message"`, message required only if something is staged) and pushes. It hard-fails if unstaged changes to tracked files are present, rather than silently deploying without them.
