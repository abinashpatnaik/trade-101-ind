"""
Deterministic synthetic OHLCV fixture generator (5m bars, 3 sessions).

Run once to (re)generate the checked-in CSVs:
    python tests/fixtures/make_fixtures.py
"""

import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BARS_PER_DAY = 75  # NSE: 09:15-15:30 = 6.25h of 5m bars
DAYS = 3


def _index():
    chunks = []
    for d in range(DAYS):
        day = pd.Timestamp("2026-07-06") + pd.Timedelta(days=d)
        start = day + pd.Timedelta(hours=9, minutes=15)
        chunks.append(pd.date_range(start, periods=BARS_PER_DAY, freq="5min"))
    return pd.DatetimeIndex(np.concatenate([c.values for c in chunks]))


def _frame(closes: np.ndarray, volumes: np.ndarray) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * 1.001
    lows = np.minimum(opens, closes) * 0.999
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes,
        },
        index=_index(),
    )


def _spiky_volume(n: int, rng: np.random.RandomState, every: int = 5) -> np.ndarray:
    """Baseline volume with a 3x conviction spike every ``every`` bars —
    required to clear the sniper volume_ratio>1.5 entry gate on spike bars."""
    vol = np.full(n, 5_000.0) * (1 + 0.1 * rng.rand(n))
    vol[::every] *= 3.0
    return vol


def uptrend() -> pd.DataFrame:
    """Steady riser with conviction volume spikes — should trade and win."""
    n = BARS_PER_DAY * DAYS
    rng = np.random.RandomState(7)
    drift = np.linspace(0, 0.08, n)                      # +8% over 3 days
    wiggle = 0.0015 * np.sin(np.arange(n) / 3.0)          # small oscillation
    noise = rng.normal(0, 0.0004, n).cumsum()
    closes = 100.0 * (1.0 + drift + wiggle + noise)
    return _frame(closes, _spiky_volume(n, rng))


def chop() -> pd.DataFrame:
    """Directionless sine-wave chop with flat volume — sniper gates block."""
    n = BARS_PER_DAY * DAYS
    rng = np.random.RandomState(11)
    wave = 0.004 * np.sin(np.arange(n) / 5.0)
    noise = rng.normal(0, 0.0008, n)
    closes = 100.0 * (1.0 + wave + noise)
    volumes = np.full(n, 5_000.0) * (1 + 0.05 * rng.rand(n))
    return _frame(closes, volumes)


def crash() -> pd.DataFrame:
    """Pump then dump: rises hard for 1.5 sessions, then collapses -12%."""
    n = BARS_PER_DAY * DAYS
    rng = np.random.RandomState(13)
    split = int(n * 0.5)
    up = np.linspace(0, 0.05, split)                       # +5% ramp
    down = 0.05 + np.linspace(0, -0.17, n - split)         # collapse to -12%
    drift = np.concatenate([up, down])
    noise = rng.normal(0, 0.0004, n).cumsum()
    closes = 100.0 * (1.0 + drift + noise)
    return _frame(closes, _spiky_volume(n, rng))


def main() -> None:
    for name, builder in [("uptrend", uptrend), ("chop", chop), ("crash", crash)]:
        path = os.path.join(HERE, f"{name}.csv")
        builder().to_csv(path, index_label="Datetime")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
