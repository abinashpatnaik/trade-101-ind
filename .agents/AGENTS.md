
## Deployment Rules
- **DO NOT** deploy directly to the EC2 instance via SSH or rsync unless explicitly asked by the user.
- **ALWAYS** check in code to GitHub, as the GitHub workflow (`deploy.yml`) automatically handles deployments to the EC2 server.
