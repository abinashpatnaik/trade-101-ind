# Deployment

> ⚠️ **This is a live, real-money system** (`TRADING_MODE=live`). Deploys rebuild
> and restart the running trading stack. Treat every change as production.

## How deployment works

Deployment is **fully automated on push to `main`** via
`.github/workflows/deploy.yml`:

1. `rsync` the repo to the Hetzner host at `~/trading-agent/` (excluding `.git`,
   `.github`, `.venv`, `logs`, `data`).
2. `docker compose kill && docker compose rm -f && docker compose up -d --build`
   — a full **rebuild and restart of the entire agent stack**.

**Implication:** merging *anything* to `main` redeploys production. There is no
staging environment. **Merge structural or behavioral changes only when both
markets are closed** (weekends, or outside 09:15–15:30 IST and 09:30–16:00 ET).

Required GitHub Actions secrets: `HETZNER_HOST`, `HETZNER_USERNAME`,
`HETZNER_SSH_KEY`.

## The host

```bash
ssh -i ~/.ssh/hetzner_trade101 root@178.105.82.119     # add -o ServerAliveInterval=15
```

- Code lives at `/root/trading-agent`, mounted into every container as `/app`
  (`docker-compose.yml`). It is a deployed snapshot, **not a git checkout**.
- Persistent volumes: `logs/`, `data/` (both excluded from rsync so they survive
  deploys), plus the `redis-data` named volume.
- Containers (per market): `in-/us-trading-agent`, `in-/us-vetting`,
  `in-/us-scanner`, `in-/us-orchestrator`, `in-/us-strategy`, `in-/us-trainer`,
  and shared `t101-redis`. Images: `trading-agent-<service>-<market>:latest`.

## Runtime configuration

Per-market env is set in `docker-compose.yml`; secrets come from `.env` on the
host (Zerodha/Alpaca keys, Gmail, `TRADING_MODE`, `AI_VALIDATION_ENABLED`,
`AI_PRIMARY_DRIVER`, etc.). Never commit `.env` or keys.

## Manual operations

**Reload code without a full CI deploy** (e.g. a hotfix): `scp` the changed
file(s) to `/root/trading-agent/…`, then restart only the affected containers:

```bash
scp -i ~/.ssh/hetzner_trade101 decision_engine.py root@178.105.82.119:/root/trading-agent/
ssh -i ~/.ssh/hetzner_trade101 root@178.105.82.119 \
  'docker restart in-trading-agent us-trading-agent in-vetting us-vetting'
```

> Restarting live trading containers is disruptive mid-session — do it when the
> relevant market is closed. Some tooling intentionally gates `docker restart`
> of live containers behind an explicit approval.

**Inspect state** (no `sqlite3` CLI on the host — use `python3`):

```bash
# recent trades
python3 -c "import sqlite3;print(sqlite3.connect('/root/trading-agent/data/trading_IN.db').execute('select * from trades order by rowid desc limit 5').fetchall())"
# live logs (wiped on container restart; the persistent logs/agent_*.log is coarser)
docker logs --since 30m in-trading-agent
```

**Run ad-hoc analysis** in a one-off container using a vetting image (they carry
the ML models + all deps), mounting the code read-through:

```bash
docker run --rm -e TRADING_MARKET=IN -e AI_VALIDATION_ENABLED=true -e AI_PRIMARY_DRIVER=true \
  -v /root/trading-agent:/app -w /app trading-agent-vetting-in:latest python -u my_script.py
# US needs Alpaca keys: add  --env-file /root/trading-agent/.env  and use ...-vetting-us:latest
```

Long SSH commands can drop (exit 255) — prefer
`nohup docker run … > log 2>&1 &` and poll the log. Always clean up temporary
scripts/logs/JSON you create on the host.

## Rollback

Revert the offending commit on `main` (`git revert <sha>`) and push — the deploy
workflow redeploys the reverted state. For a fast in-place fix, `scp` the known-
good file and restart the affected containers (see above).

> A more detailed (and partially historical, EC2-era) guide is preserved in
> [`CLOUD_DEPLOYMENT.md`](CLOUD_DEPLOYMENT.md). This document is authoritative
> for the current Hetzner setup.
