# trade-101 — Multi-Agent Trading System

An autonomous, multi-agent trading system running two independent markets from
one codebase:

- **India** — NSE via Zerodha Kite Connect
- **US** — NASDAQ via Alpaca

The market is chosen at process start by `TRADING_MARKET` (`IN` / `US`). Each
market runs its own agent stack — orchestrator, trader, scanner, vetting,
strategy, trainer — coordinating over a shared Redis bus, with XGBoost models
driving entries and a friction-aware decision engine gating them.

> ⚠️ Runs **live with real money** by default (`TRADING_MODE=live`). Every change
> to `main` auto-deploys to production. Read [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)
> before pushing.

## Documentation

| Doc | What's in it |
|-----|--------------|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The agent stack, decision flow, core modules, and data/state model. |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | The Hetzner auto-deploy pipeline, host layout, manual ops, and rollback. |
| [`scripts/README.md`](scripts/README.md) | The one-off / diagnostic utilities (not production). |

## Repository layout

```
agents/        Multi-agent runtime — one module per agent (python -m agents.<name>)
*.py (root)    Core modules, imported flatly (config, decision_engine, order_executor, …)
tests/         Automated test suite (pytest)
scripts/       One-off / manual utilities (checks/, analysis/, maintenance/) — not shipped in prod
dashboard/     Web dashboard
docs/          Architecture & deployment docs
data/          Runtime data: models, thresholds, daily targets, SQLite DBs (git-ignored contents)
```

Core modules live flat at the root by design (the Dockerfile does `COPY *.py .`
and agents import them flatly). See the note in `docs/ARCHITECTURE.md`.

## Quickstart (local, paper mode)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt        # -r requirements-dev.txt for tests

cp .env.example .env        # fill in broker/API credentials; set TRADING_MODE=paper
export TRADING_MARKET=IN    # or US

python -m agents.trader     # run a single agent, or use docker compose for the full stack
```

## Running the full stack (Docker Compose)

```bash
docker compose up -d --build          # all agents, both markets, + Redis
docker compose logs -f in-trading-agent
docker compose down
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/
```

The `scripts/checks/` directory holds ad-hoc connectivity checks (broker logins,
etc.) that need live credentials — those are **not** part of the pytest suite.

## Configuration

Runtime config is centralised in `config.py` (per-market profiles) and driven by
environment variables. Key ones: `TRADING_MARKET`, `TRADING_MODE`,
`AI_VALIDATION_ENABLED`, `AI_PRIMARY_DRIVER`, plus broker credentials. See
`.env.example` and `docs/DEPLOYMENT.md`.
