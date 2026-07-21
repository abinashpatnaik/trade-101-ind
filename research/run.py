"""
research.run
============
CLI entry point — the research agent.

    python -m research.run --market US --signal momentum20
    python -m research.run --market US --signal all --windows 12
    python -m research.run --market IN --signal pullback_uptrend --symbols-file syms.txt

Run it in the vetting image so the ML models and dependencies are present:

    docker run --rm -e TRADING_MARKET=US -e AI_VALIDATION_ENABLED=true \\
      -e AI_PRIMARY_DRIVER=true --env-file /root/trading-agent/.env \\
      -v /root/trading-agent:/app -w /app trading-agent-vetting-us:latest \\
      python -m research.run --market US --signal all

Every verdict is produced by ``research.guards``, so a result cannot be
reported without its cluster-aware CI, drop-best-symbol check, matched-random
control, and effect-size-versus-friction comparison. When several signals are
run together the multiple-comparisons count is set automatically — reporting
the best of five without saying five were tried is how noise gets promoted.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import List

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("research")


def _default_symbols(market: str) -> List[str]:
    from config import config
    return list(config.universe.tickers)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Point-in-time signal research")
    ap.add_argument("--market", default=os.getenv("TRADING_MARKET", "IN"),
                    choices=["IN", "US"])
    ap.add_argument("--signal", default="all",
                    help="signal name from research.signals.CATALOGUE, or 'all'")
    ap.add_argument("--windows", type=int, default=8)
    ap.add_argument("--window-days", type=int, default=10)
    ap.add_argument("--top-n", type=int, default=15)
    ap.add_argument("--symbols-file", help="newline-delimited symbols; defaults to config universe")
    ap.add_argument("--min-price", type=float, default=None)
    ap.add_argument("--min-volume", type=float, default=None)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    from research.harness import run_study
    from research.signals import CATALOGUE

    if args.signal == "all":
        chosen = list(CATALOGUE.values())
    elif args.signal in CATALOGUE:
        chosen = [CATALOGUE[args.signal]]
    else:
        log.error("unknown signal %r; available: %s",
                  args.signal, ", ".join(sorted(CATALOGUE)))
        return 2

    if args.symbols_file:
        with open(args.symbols_file) as fh:
            symbols = [ln.strip() for ln in fh if ln.strip()]
    else:
        symbols = _default_symbols(args.market)
    log.info("market=%s symbols=%d signals=%d windows=%d",
             args.market, len(symbols), len(chosen), args.windows)

    # US prices/volumes are on a different scale than INR.
    min_price = args.min_price if args.min_price is not None else (10.0 if args.market == "US" else 50.0)
    min_volume = args.min_volume if args.min_volume is not None else 1_000_000.0

    reports, payload = [], {}
    for sig in chosen:
        log.info("=== %s ===", sig.name)
        try:
            study = run_study(
                sig, symbols, market=args.market, n_windows=args.windows,
                window_days=args.window_days, top_n=args.top_n,
                min_price=min_price, min_volume=min_volume,
                cache_dir=args.cache_dir,
            )
        except Exception as exc:
            log.error("study failed for %s: %s", sig.name, exc)
            continue
        rep = study.report(n_variants_tried=len(chosen))
        reports.append(rep)
        payload[sig.name] = {
            "trades": rep.n_trades, "clusters": rep.n_clusters,
            "net_total": rep.net_total, "net_per_trade": rep.net_per_trade,
            "ci": [rep.ci_low, rep.ci_high], "p_vs_random": rep.p_vs_random,
            "without_best_cluster": rep.without_best_cluster,
            "monotonic": rep.monotonic, "passed": rep.passed,
            "notes": rep.notes, "picks": study.picks,
        }

    print("\n" + "=" * 78)
    print(f"RESEARCH VERDICTS — market={args.market}, {args.windows} x "
          f"{args.window_days}d point-in-time windows")
    print("=" * 78)
    for rep in reports:
        print(rep.render())
        print("-" * 78)

    survivors = [r.name for r in reports if r.passed]
    print(f"\nSurvived every guard: {survivors or 'NONE'}")
    if not survivors:
        print("No signal cleared the bar. That is the expected result for a "
              "hypothesis with no edge — and the point of running this first.")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(payload, fh, indent=1)
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
