# Zeev Project Rules

## Deployment Constraints
Before running `./deploy.sh`, you MUST ensure that all intended local modifications (e.g., changes to Python scripts, configurations) are committed to git. 

The `deploy.sh` script executes a `git push origin main` and relies on pulling from the remote repository on the target device. If you execute the script with uncommitted changes, those changes will not be pushed and the deployment will not reflect your latest work.
