"""
research.guards
===============
The statistical guards that caught every false positive this project produced.

Each one exists because a real result fooled us:

- ``cluster_bootstrap_ci`` — a per-symbol edge score showed +0.22% net and
  "beat random" at P=0.02. Trades are clustered by symbol/window; an i.i.d.
  bootstrap understates variance. The cluster CI was [-1.4, +2.1].
- ``drop_best_contributor``  — that same +0.22% became -1.30% once SAREGAMA was
  removed, and an entry gate's +7.08% became -2.15% without one symbol.
- ``monotonicity`` — real signal strengthens as you select harder. The entry
  gate's top-10% (-8.05) was WORSE than its top-20% (+7.08).
- ``matched_random_control`` — taking fewer trades mechanically cuts friction
  losses, so beating "all trades" proves nothing. It must beat a random basket
  of the SAME SIZE.
- ``multiple_comparisons_note`` — trying 5 thresholds and reporting the best
  gives P(one looks significant at 0.05) ~ 0.23.

Also: an effect can be measured precisely and still be useless. Always compare
the effect SIZE against ``friction``; regime timing died on this — its best
split was 0.11% against a 0.22% hurdle, and no extra sample could fix that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np


@dataclass
class GuardReport:
    """Verdict for one candidate signal. ``passed`` is deliberately strict."""
    name: str
    n_trades: int
    n_clusters: int
    net_total: float
    net_per_trade: float
    ci_low: float
    ci_high: float
    p_vs_random: float
    without_best_cluster: float
    monotonic: Optional[bool]
    friction: float
    notes: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        # bool() matters: these comparisons yield numpy scalars, and np.False_
        # is not False — callers doing `is False` would silently misread it.
        return bool(
            self.ci_low > 0.0
            and self.p_vs_random < 0.05
            and self.without_best_cluster > 0.0
            and (self.monotonic is not False)
        )

    def render(self) -> str:
        v = "PASS" if self.passed else "FAIL"
        lines = [
            f"[{v}] {self.name}",
            f"  trades={self.n_trades}  clusters={self.n_clusters}",
            f"  net={self.net_total:+.2f}%  per-trade={self.net_per_trade:+.4f}%"
            f"  (friction {self.friction:.3f}%)",
            f"  cluster-bootstrap 95% CI: [{self.ci_low:+.2f}, {self.ci_high:+.2f}]",
            f"  P(matched-random >= this): {self.p_vs_random:.3f}",
            f"  net excluding best cluster: {self.without_best_cluster:+.2f}%",
            f"  monotonic across thresholds: {self.monotonic}",
        ]
        lines += [f"  ! {n}" for n in self.notes]
        return "\n".join(lines)


def cluster_bootstrap_ci(returns_by_cluster: Dict[str, Sequence[float]],
                         n_boot: int = 5000, seed: int = 0,
                         scale_to: Optional[int] = None) -> tuple:
    """
    Resample whole CLUSTERS (symbol-window blocks) with replacement.

    Trades within a symbol on one day are not independent — they share the
    same setup and the same price path. Resampling individual trades pretends
    they are, producing intervals that are far too narrow.
    """
    rng = np.random.default_rng(seed)
    blocks = [np.asarray(v, dtype=float) for v in returns_by_cluster.values() if len(v)]
    if not blocks:
        return (0.0, 0.0)
    out = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, len(blocks), len(blocks))
        vals = np.concatenate([blocks[i] for i in pick])
        out[b] = vals.sum() * (scale_to / len(vals)) if scale_to else vals.sum()
    return tuple(np.percentile(out, [2.5, 97.5]))


def drop_best_contributor(returns_by_cluster: Dict[str, Sequence[float]]) -> float:
    """Total net with the single best-contributing cluster removed.

    A result that depends on one symbol is that symbol's luck, not a signal.
    """
    sums = {k: float(np.sum(v)) for k, v in returns_by_cluster.items() if len(v)}
    if not sums:
        return 0.0
    total = sum(sums.values())
    return total - max(sums.values())


def matched_random_control(all_returns: Sequence[float], k: int,
                           observed_total: float, n_boot: int = 5000,
                           seed: int = 0,
                           clusters: Optional[Dict[str, Sequence[float]]] = None
                           ) -> float:
    """
    P(a random basket of the same size does at least as well).

    When ``clusters`` is given the null draws whole blocks, matching the
    correlation structure of a real selection rule.
    """
    rng = np.random.default_rng(seed)
    if clusters:
        names = list(clusters)
        sims = np.empty(n_boot)
        for b in range(n_boot):
            rng.shuffle(names)
            tot, n = 0.0, 0
            for c in names:
                v = clusters[c]
                tot += float(np.sum(v)); n += len(v)
                if n >= k:
                    break
            sims[b] = tot * (k / max(n, 1))
        return float((sims >= observed_total).mean())

    arr = np.asarray(all_returns, dtype=float)
    if k >= len(arr):
        return 1.0
    sims = np.array([arr[rng.choice(len(arr), k, replace=False)].sum()
                     for _ in range(n_boot)])
    return float((sims >= observed_total).mean())


def monotonicity(totals_by_threshold: Dict[float, float]) -> Optional[bool]:
    """
    True when per-trade performance improves as selection tightens.

    Genuine signal concentrates; noise wanders. Needs >= 3 thresholds.
    """
    if len(totals_by_threshold) < 3:
        return None
    ordered = [totals_by_threshold[k] for k in sorted(totals_by_threshold, reverse=True)]
    return all(b >= a for a, b in zip(ordered, ordered[1:]))


def multiple_comparisons_note(n_tried: int, alpha: float = 0.05) -> Optional[str]:
    if n_tried <= 1:
        return None
    p_any = 1.0 - (1.0 - alpha) ** n_tried
    return (f"{n_tried} variants were tried; P(>=1 spuriously significant at "
            f"{alpha}) = {p_any:.2f}. Reported p-values are optimistic.")


def evaluate(name: str, returns_by_cluster: Dict[str, Sequence[float]],
             universe_returns: Sequence[float], friction: float,
             totals_by_threshold: Optional[Dict[float, float]] = None,
             n_variants_tried: int = 1,
             universe_clusters: Optional[Dict[str, Sequence[float]]] = None,
             seed: int = 0) -> GuardReport:
    """Run every guard and return a single strict verdict."""
    flat = [r for v in returns_by_cluster.values() for r in v]
    n = len(flat)
    total = float(np.sum(flat)) if n else 0.0
    lo, hi = cluster_bootstrap_ci(returns_by_cluster, seed=seed)
    p = matched_random_control(universe_returns, n, total, seed=seed,
                               clusters=universe_clusters)
    rep = GuardReport(
        name=name, n_trades=n, n_clusters=len(returns_by_cluster),
        net_total=total, net_per_trade=(total / n if n else 0.0),
        ci_low=lo, ci_high=hi, p_vs_random=p,
        without_best_cluster=drop_best_contributor(returns_by_cluster),
        monotonic=monotonicity(totals_by_threshold or {}),
        friction=friction,
    )
    note = multiple_comparisons_note(n_variants_tried)
    if note:
        rep.notes.append(note)
    if n and abs(total / n) < friction:
        rep.notes.append(
            f"effect per trade ({total/n:+.4f}%) is smaller than friction "
            f"({friction:.3f}%) — more data raises precision, not magnitude.")
    if rep.n_clusters < 20:
        rep.notes.append(f"only {rep.n_clusters} clusters — CI is wide by construction.")
    return rep
