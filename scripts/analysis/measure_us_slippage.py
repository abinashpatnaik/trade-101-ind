"""
measure_us_slippage.py
=======================
Measures real US BUY-leg slippage against Alpaca's actual fills, instead of
trusting the flat ASSUMED_SLIPPAGE_PER_LEG guess in trading_costs.py.

data/order_intents_US.csv records the SIGNAL price at order-placement time
for every live BUY (see OrderExecutor._record_order_intent). Alpaca's own
order history has the authoritative filled_avg_price for the same order_id.
Real slippage per BUY leg = (filled_avg_price - signal_price) / signal_price.

First run (2026-09-23, n=88): mean -0.022%, 95% CI [-0.13%, +0.08%], robust
to dropping the 3 best/worst fills (+0.013%) -- the CI excludes the 0.1%/leg
that was previously assumed (carried over from IN, never measured for US).
US_ASSUMED_SLIPPAGE_PER_LEG in trading_costs.py was set to 0.08%, the CI's
conservative upper bound. Re-run this periodically (more live fills = a
tighter CI) and revisit that constant if the estimate moves.

Only measures the BUY leg -- order_executor.py does not log a signal price
for SELLs, so exit-side slippage is still unmeasured and assumed similar.

Run in the vetting-us image (Alpaca creds + deps), or locally with
APCA_API_KEY_ID/APCA_API_SECRET_KEY set:

    .venv/bin/python -m scripts.analysis.measure_us_slippage
"""
from __future__ import annotations

import csv
import os
import statistics
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_IN_DOCKER = os.path.exists("/app")
_DATA_DIR = "/app/data" if _IN_DOCKER else os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"
)


def main() -> int:
    from alpaca.trading.client import TradingClient

    csv_path = os.path.join(_DATA_DIR, "order_intents_US.csv")
    if not os.path.exists(csv_path):
        print(f"no order intents found at {csv_path}")
        return 1

    client = TradingClient(
        os.environ["APCA_API_KEY_ID"], os.environ["APCA_API_SECRET_KEY"], paper=False
    )

    rows = [r for r in csv.DictReader(open(csv_path)) if r.get("action") == "BUY"]
    print(f"{len(rows)} recorded BUY order intents")

    slips = []
    misses = 0
    for r in rows:
        try:
            order = client.get_order_by_id(r["order_id"])
        except Exception:
            misses += 1
            continue
        if order.status.value != "filled" or order.filled_avg_price is None:
            misses += 1
            continue
        signal_price = float(r["signal_price"])
        fill = float(order.filled_avg_price)
        slips.append({
            "symbol": r["symbol"], "ts": r["ts"], "signal": signal_price,
            "fill": fill, "slip_pct": (fill - signal_price) / signal_price * 100.0,
        })

    print(f"resolved {len(slips)} filled orders, {misses} unresolved")
    if not slips:
        print("nothing to measure")
        return 0

    vals = np.array([s["slip_pct"] for s in slips])
    n = len(vals)
    mean, se = vals.mean(), vals.std(ddof=1) / np.sqrt(n)
    rng = np.random.default_rng(0)
    boot = np.array([rng.choice(vals, n, replace=True).mean() for _ in range(10000)])
    lo, hi = np.percentile(boot, [2.5, 97.5])
    trimmed = sorted(vals)[3:-3] if n > 6 else vals

    print(f"\nBUY-leg slippage (fill vs signal price), n={n}")
    print(f"  mean {mean:+.4f}%  median {statistics.median(vals):+.4f}%  "
          f"stdev {vals.std():.4f}%")
    print(f"  analytical 95% CI [{mean - 1.96*se:+.4f}%, {mean + 1.96*se:+.4f}%]")
    print(f"  bootstrap  95% CI [{lo:+.4f}%, {hi:+.4f}%]")
    print(f"  trimmed mean (drop 3 best + 3 worst): {np.mean(trimmed):+.4f}%")

    from trading_costs import US_ASSUMED_SLIPPAGE_PER_LEG
    assumed_pct = US_ASSUMED_SLIPPAGE_PER_LEG * 100
    print(f"\n  currently assumed: {assumed_pct:.4f}%/leg")
    if assumed_pct > hi:
        print(f"  -> still conservative (above the bootstrap CI upper bound {hi:+.4f}%)")
    elif assumed_pct < lo:
        print(f"  -> NOW UNDER-STATING cost (below the CI lower bound {lo:+.4f}%) — revisit")
    else:
        print(f"  -> inside the measured CI")

    by_sym = defaultdict(list)
    for s in slips:
        by_sym[s["symbol"]].append(s["slip_pct"])
    print("\nby symbol (n>=2), worst mean first:")
    for sym, v in sorted(by_sym.items(), key=lambda kv: -statistics.mean(kv[1])):
        if len(v) >= 2:
            print(f"  {sym:<6} n={len(v):2d}  mean={statistics.mean(v):+.4f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
