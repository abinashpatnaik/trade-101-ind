"""
research
========
Offline research harness. Imported by nothing in the live trading path — it
exists so a new signal hypothesis can be tested in minutes instead of a day,
with the anti-fooling guards applied automatically rather than remembered.

Modules
-------
- ``guards``   — cluster bootstrap, drop-best-contributor, monotonicity,
                 matched-random control, multiple-comparisons accounting.
- ``windows``  — point-in-time window construction (rank before a cutoff,
                 evaluate strictly after it).
- ``signal``   — the ``Signal`` protocol a hypothesis implements.

Why this exists
---------------
Six candidate edges were tested by hand in this project (selection rules, exit
timing, per-symbol edge scores, an entry meta-label gate, five-year strategy
edge, regime timing). All measured zero after guards. Three of them looked
significant first and were only unmasked because someone remembered to run a
cluster bootstrap or drop the best symbol. Encoding those checks means the next
hypothesis cannot skip them.

The bar any signal must clear is not "positive" — it is "positive by more than
friction, with a cluster-aware CI excluding zero, not carried by one symbol,
and monotonic as selection tightens".
"""

from research.guards import GuardReport, evaluate  # noqa: F401
from research.signal import Signal  # noqa: F401
from research.windows import Window, point_in_time_windows  # noqa: F401

__all__ = ["GuardReport", "evaluate", "Signal", "Window", "point_in_time_windows"]
