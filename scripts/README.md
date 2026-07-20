# scripts/

One-off, manual, and diagnostic utilities. **None of these run in production** —
the live agent stack (`agents/`) never imports them, and they are excluded from
the Docker image. They are kept for occasional manual use and historical
reference.

Because the codebase uses flat imports (`from config import config`), run these
from the **repository root** with the root on `PYTHONPATH`:

```bash
PYTHONPATH=. python scripts/checks/test_alpaca.py
```

| Directory | Contents |
|-----------|----------|
| `checks/` | Ad-hoc connectivity / sanity checks (broker logins, config, ML pipeline, timezone). Require live credentials in `.env`. **Not** the automated test suite — that lives in `tests/` and runs with `pytest`. |
| `analysis/` | Offline analysis and ad-hoc simulations (`analyze_*`, `sim_*`, `run_full_sim`, `query_alpaca`). Some are historical and may reference APIs that have since changed. |
| `maintenance/` | One-time data/state fixes and migrations (`fix_*`, `migrate_csv_to_db`). Most were applied once; treat as historical unless you know you need them. |

> The recurring P&L backfill (`backfill_pnl.py`) deliberately stays at the repo
> root — it is executed **inside the running container** by
> `.github/workflows/backfill.yml` (`docker exec … python backfill_pnl.py`), so
> it must ship in the image via the Dockerfile's `COPY *.py .`.
