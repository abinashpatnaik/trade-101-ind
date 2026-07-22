"""
research.run_flow
=================
Re-validate the month-end Treasury duration flow against live data.

    python -m research.run_flow                      # full battery, TLT primary
    python -m research.run_flow --symbol EDV
    python -m research.run_flow --entry 12 --exit 0  # test other offsets

Run it in the US vetting image (Alpaca keys + deps):

    docker run --rm -e TRADING_MARKET=US --env-file /root/trading-agent/.env \\
      -v /root/trading-agent:/app -w /app trading-agent-vetting-us:latest \\
      python -m research.run_flow

Intended to be re-run periodically: each new month adds a trade, and the
battery re-checks that the edge still holds rather than assuming it does. A
published anomaly can decay, and the walk-forward block is where that would
show up first.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Dict

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("run_flow")

# Approximate effective duration in years — the mechanism's predicted ordering.
DURATIONS: Dict[str, float] = {
    "EDV": 24.0, "TLT": 17.0, "VGLT": 15.0, "TLH": 9.0, "IEF": 7.0, "SHY": 1.9,
}
CONTROL = "SPY"          # non-bond control: must NOT show a real month-end effect
ERAS = [(2002, 2009), (2010, 2017), (2018, 2026)]


def _load(symbols, cache_dir=None) -> Dict[str, pd.DataFrame]:
    """Daily bars. Uses research.data when available, else yfinance directly."""
    out: Dict[str, pd.DataFrame] = {}
    try:
        from research.data import BarSource
        src = BarSource("US", cache_dir) if cache_dir else BarSource("US")
        out = src.daily(list(symbols), years=25)
        out = {k: v for k, v in out.items() if v is not None and len(v) > 500}
    except Exception as exc:
        log.info("BarSource unavailable (%s) — falling back to yfinance", exc)
    missing = [s for s in symbols if s not in out]
    if missing:
        import yfinance as yf
        for s in missing:
            try:
                df = yf.download(s, start="2002-01-01", progress=False,
                                 auto_adjust=True)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.dropna(subset=["Close"])
                if len(df) > 500:
                    out[s] = df
            except Exception as exc:
                log.warning("could not load %s: %s", s, exc)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate the month-end bond flow")
    ap.add_argument("--symbol", default="TLT", help="primary instrument")
    ap.add_argument("--entry", type=int, default=7, help="trading days before month end")
    ap.add_argument("--exit", dest="exit_", type=int, default=1)
    ap.add_argument("--friction", type=float, default=None,
                    help="round-trip cost %%; defaults to the US model")
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    from research.calendar_flow import MonthEndFlow, rest_of_month_control, validate

    if args.friction is not None:
        friction = args.friction
    else:
        try:
            from agents.backtest_sim import SimParams
            friction = SimParams().round_trip_cost_pct * 100.0
        except Exception:
            friction = 0.220
    log.info("friction assumption: %.3f%% per round trip", friction)

    symbols = sorted(set(list(DURATIONS) + [CONTROL, args.symbol]))
    frames = _load(symbols, args.cache_dir)
    if args.symbol not in frames:
        log.error("no data for primary symbol %s", args.symbol)
        return 2
    log.info("loaded %d/%d instruments", len(frames), len(symbols))

    flow = MonthEndFlow(entry_days_before_end=args.entry,
                        exit_days_before_end=args.exit_)
    control_res = (rest_of_month_control(frames[CONTROL], flow)
                   if CONTROL in frames else None)

    rep = validate(frames[args.symbol], flow, friction, ERAS,
                   frames=frames, durations=DURATIONS,
                   control_symbol_result=control_res)

    print("\n" + "=" * 84)
    print(f"MONTH-END FLOW VALIDATION — {args.symbol}, "
          f"entry={args.entry} exit={args.exit_}")
    print("=" * 84)
    print(rep.render())
    print("-" * 84)

    if control_res:
        print(f"\n{CONTROL} (non-bond control): month-end "
              f"{control_res['month_end_pct']:+.3f}% vs elsewhere "
              f"{control_res['other_pct']:+.3f}%, t={control_res['t_stat']:.2f}")
        print("  (must be NOT significant — otherwise this is not a bond flow)")

    if rep.gradient.get("points"):
        print("\nduration gradient:")
        for dur, val in sorted(rep.gradient["points"], reverse=True):
            print(f"  {dur:>5.1f}y  {val:+.3f}%/trade")

    per_year = rep.gross_avg_pct * 12
    print(f"\nexpectation: ~{per_year:.2f}%/yr gross at 12 trades/yr; "
          f"~{(rep.gross_avg_pct - friction) * 12:.2f}%/yr net at the "
          f"{friction:.3f}% assumption")
    print(f"break-even round-trip cost: {rep.gross_avg_pct:.3f}%")
    print("\nNOTE: this is a modest, real edge — not the headline number that "
          "prompted it.\nIt needs execution cheaper than break-even, and capital "
          "for the return to matter.")

    if args.json_out:
        payload = {k: v for k, v in rep.__dict__.items()}
        payload["passed"] = rep.passed
        with open(args.json_out, "w") as fh:
            json.dump(payload, fh, indent=1, default=str)
        print(f"\nwrote {args.json_out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
