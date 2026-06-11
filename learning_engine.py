"""
learning_engine.py
==================
Self-learning feedback loop for the sentiment engine.

After every closed trade the agent calls LearningEngine.on_trade_closed().
This engine:

  1. Keyword weight learning
     Fetches the news headlines that were available at trade entry time,
     identifies which keywords appeared, and nudges their weights up or down
     based on whether the trade was profitable.
     Weights are persisted to keyword_weights.json.

  2. Signal accuracy tracking
     Records the sentiment score and trend score at entry for every trade.
     After 10+ trades, computes per-score-bucket accuracy and adjusts the
     sentiment weight in the combined signal accordingly.
     Stored in signal_accuracy.json.

  3. Threshold recalibration
     After every 20 closed trades, recomputes optimal buy/sell thresholds
     by finding the score cutoffs that maximised win rate on recent trades.
     Stored in calibration.json and applied live to config.signal.

  All data files are stored in the same directory as trades.csv.
"""

from __future__ import annotations

import logging
import os
import re
import string
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base word lists (mirrors sentiment_engine.py — used to seed learning)
# ---------------------------------------------------------------------------

_BASE_POSITIVE: List[str] = [
    "surge", "soar", "record", "beat", "upgrade", "buy", "outperform",
    "profit", "growth", "strong", "rally", "gain", "rise", "jumps", "boost",
    "up", "increase", "positive", "higher", "above", "exceed", "good", "well",
]

_BASE_NEGATIVE: List[str] = [
    "down", "fall", "drop", "miss", "below", "weak", "concern", "cut",
    "crash", "plunge", "collapse", "loss", "downgrade", "sell", "underperform",
    "warning", "risk", "decline", "slump", "tumble",
]

_BASE_WORD_SET: set = set(_BASE_POSITIVE) | set(_BASE_NEGATIVE)

# Learning rate — gentle nudges to avoid overfitting single trades
_LEARNING_RATE: float = 0.05

# Minimum appearances across trades before we update a word's weight
_MIN_TRADE_COUNT: int = 3


def _extract_words(text: str) -> List[str]:
    """Lowercase, strip punctuation, return words of at least 4 chars."""
    text = text.lower()
    # Replace punctuation with spaces
    text = text.translate(str.maketrans(string.punctuation, " " * len(string.punctuation)))
    return [w for w in text.split() if len(w) >= 4]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class LearningEngine:
    """
    Self-learning feedback loop for keyword weights, signal accuracy
    tracking, and threshold recalibration.
    """

    def __init__(self) -> None:
        # Paths — use same dir as trades.csv
        data_dir = os.path.dirname(config.agent.trades_csv) or "."
        self._weights_path = os.path.join(data_dir, "keyword_weights.json")
        self._accuracy_path = os.path.join(data_dir, "signal_accuracy.json")
        self._calibration_path = os.path.join(data_dir, "calibration.json")

        # In-memory state
        self._keyword_weights: Dict[str, float] = {}
        self._signal_records: List[Dict] = []  # rolling window of trade records
        self._calibration: Dict = {}

        # Track how many distinct trades each word has appeared in
        self._word_trade_count: Dict[str, int] = {}

        self._load_all()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_trade_closed(
        self,
        symbol: str,
        action: str,
        pnl: float,
        pnl_pct: float,
        sentiment_score: float,
        trend_score: float,
        combined_score: float,
        headlines: List[str],
    ) -> None:
        """
        Called by agent.py after every SELL trade closes.

        Parameters
        ----------
        symbol:         Ticker symbol (e.g. 'BARC')
        action:         Trade side — always 'SELL' for closed trades
        pnl:            Realised P&L in INR (positive = profit)
        pnl_pct:        Realised P&L as a percentage
        sentiment_score: Sentiment score at entry
        trend_score:    Trend overall score at entry
        combined_score: Combined decision score at entry
        headlines:      List of news headline strings seen at entry
        """
        was_profitable = pnl > 0

        # 1. Update keyword weights from headlines
        n_updated = self._update_keyword_weights(headlines, was_profitable)

        # 2. Record signal accuracy
        self._record_signal_accuracy(
            symbol, sentiment_score, trend_score, combined_score, pnl, pnl_pct
        )

        # 3. Every 20 trades: recalibrate thresholds
        if len(self._signal_records) > 0 and len(self._signal_records) % 20 == 0:
            self._recalibrate_thresholds()

        # 4. Persist
        self._save_all()

        outcome = "WIN" if was_profitable else "LOSS"
        logger.info(
            "Learning update: %s %s ₹%.2f — updated %d keyword weights",
            symbol, outcome, pnl, n_updated,
        )

    def get_keyword_score(self, text: str) -> float:
        """
        Score *text* using learned keyword weights.

        Returns average weight of matched words, clamped to [-1, 1].
        Falls back to 0.0 if no matches.
        """
        if not self._keyword_weights:
            return 0.0

        words = _extract_words(text)
        matched: List[float] = []
        for word in words:
            if word in self._keyword_weights:
                matched.append(self._keyword_weights[word])

        if not matched:
            return 0.0

        return _clamp(sum(matched) / len(matched), -1.0, 1.0)

    def get_learned_weights(self) -> Dict[str, Any]:
        """
        Return top 20 positive and top 20 negative learned keywords.

        Returns
        -------
        dict with keys:
          "positive": [{"word": str, "weight": float}, ...]
          "negative": [{"word": str, "weight": float}, ...]
        """
        positive = sorted(
            [{"word": w, "weight": round(v, 4)} for w, v in self._keyword_weights.items() if v > 0],
            key=lambda x: x["weight"],
            reverse=True,
        )[:20]

        negative = sorted(
            [{"word": w, "weight": round(v, 4)} for w, v in self._keyword_weights.items() if v < 0],
            key=lambda x: x["weight"],
        )[:20]

        return {"positive": positive, "negative": negative}

    def get_accuracy_stats(self) -> Dict[str, Any]:
        """
        Return a summary of learning engine state and accuracy statistics.
        """
        records = self._signal_records
        total = len(records)
        wins = sum(1 for r in records if r.get("win", False))
        win_rate = (wins / total) if total > 0 else 0.0
        avg_pnl = (sum(r["pnl"] for r in records) / total) if total > 0 else 0.0

        # Score bucket stats
        bucket_map: Dict[float, Dict] = {}
        for r in records:
            score = r.get("combined_score", 0.0)
            bucket = round(int(score * 10) / 10, 1)  # e.g. 0.43 → 0.4
            if bucket not in bucket_map:
                bucket_map[bucket] = {"bucket": bucket, "trades": 0, "wins": 0}
            bucket_map[bucket]["trades"] += 1
            if r.get("win", False):
                bucket_map[bucket]["wins"] += 1

        score_bucket_stats = [
            {
                "bucket": bkt,
                "trades": d["trades"],
                "win_rate": round(d["wins"] / d["trades"], 3) if d["trades"] > 0 else 0.0,
            }
            for bkt, d in sorted(bucket_map.items())
        ]

        # Top keywords
        all_weights = self._keyword_weights
        top_positive = sorted(
            [{"word": w, "weight": round(v, 4)} for w, v in all_weights.items() if v > 0],
            key=lambda x: x["weight"],
            reverse=True,
        )[:10]
        top_negative = sorted(
            [{"word": w, "weight": round(v, 4)} for w, v in all_weights.items() if v < 0],
            key=lambda x: x["weight"],
        )[:10]

        return {
            "total_trades": total,
            "win_rate": round(win_rate, 4),
            "avg_pnl": round(avg_pnl, 2),
            "current_buy_threshold": config.signal.buy_threshold,
            "current_sell_threshold": config.signal.sell_threshold,
            "score_bucket_stats": score_bucket_stats,
            "top_positive_keywords": top_positive,
            "top_negative_keywords": top_negative,
        }

    # ------------------------------------------------------------------
    # Private: keyword weight learning
    # ------------------------------------------------------------------

    def _update_keyword_weights(self, headlines: List[str], was_profitable: bool) -> int:
        """
        Nudge keyword weights based on trade outcome.

        Returns the number of weights updated.
        """
        # Collect all words across all headlines for this trade
        all_words: set = set()
        for headline in headlines:
            for word in _extract_words(headline):
                all_words.add(word)

        # Update trade count for each word
        for word in all_words:
            self._word_trade_count[word] = self._word_trade_count.get(word, 0) + 1

        n_updated = 0
        for word in all_words:
            # Only update if word has appeared in at least MIN_TRADE_COUNT trades
            in_base = word in _BASE_WORD_SET
            count = self._word_trade_count.get(word, 0)

            if not (in_base or count >= _MIN_TRADE_COUNT):
                continue

            current = self._keyword_weights.get(word, 0.0)

            if was_profitable:
                # Nudge toward +1
                new_weight = current + _LEARNING_RATE * (1.0 - current)
            else:
                # Nudge toward -1
                new_weight = current - _LEARNING_RATE * (1.0 + current)

            # Initialise new words that aren't in the dict yet
            if word not in self._keyword_weights:
                new_weight = 0.1 if was_profitable else -0.1

            self._keyword_weights[word] = _clamp(new_weight, -1.0, 1.0)
            n_updated += 1

        return n_updated

    # ------------------------------------------------------------------
    # Private: signal accuracy recording
    # ------------------------------------------------------------------

    def _record_signal_accuracy(
        self,
        symbol: str,
        sentiment_score: float,
        trend_score: float,
        combined_score: float,
        pnl: float,
        pnl_pct: float,
    ) -> None:
        """Append a trade record to the rolling accuracy window."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "sentiment_score": round(sentiment_score, 3),
            "trend_score": round(trend_score, 3),
            "combined_score": round(combined_score, 3),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "win": pnl > 0,
        }
        self._signal_records.append(record)

        # Rolling window: keep last 500 records
        if len(self._signal_records) > 500:
            self._signal_records = self._signal_records[-500:]

    # ------------------------------------------------------------------
    # Private: threshold recalibration
    # ------------------------------------------------------------------

    def _recalibrate_thresholds(self) -> None:
        """
        Recompute optimal buy/sell thresholds from recent trade history.

        Requires at least 20 records. Finds the score bucket cutoffs that
        maximise win rate and applies them live to config.signal.
        """
        records = self._signal_records
        if len(records) < 20:
            logger.debug("Skipping threshold recalibration — need 20+ records (have %d).", len(records))
            return

        # Build per-bucket stats
        bucket_map: Dict[float, Dict] = {}
        for r in records:
            score = r.get("combined_score", 0.0)
            # Round down to nearest 0.1 bucket
            bucket = round(int(score * 10) / 10, 1)
            if bucket not in bucket_map:
                bucket_map[bucket] = {"total": 0, "wins": 0}
            bucket_map[bucket]["total"] += 1
            if r.get("win", False):
                bucket_map[bucket]["wins"] += 1

        # --- Buy threshold: lowest positive bucket with win_rate >= 55% ---
        new_buy_threshold: Optional[float] = None
        for bucket in sorted(b for b in bucket_map if b > 0):
            stats = bucket_map[bucket]
            if stats["total"] > 0:
                win_rate = stats["wins"] / stats["total"]
                if win_rate >= 0.55:
                    new_buy_threshold = bucket
                    break  # lowest qualifying bucket

        # --- Sell threshold: highest negative bucket with loss_rate >= 55% ---
        new_sell_threshold: Optional[float] = None
        for bucket in sorted((b for b in bucket_map if b < 0), reverse=True):
            stats = bucket_map[bucket]
            if stats["total"] > 0:
                loss_rate = 1.0 - (stats["wins"] / stats["total"])  # losses / total
                if loss_rate >= 0.55:
                    new_sell_threshold = bucket
                    break  # highest qualifying bucket

        # Apply bounds
        old_buy = config.signal.buy_threshold
        old_sell = config.signal.sell_threshold

        if new_buy_threshold is not None:
            new_buy_threshold = _clamp(new_buy_threshold, 0.25, 0.70)
        else:
            new_buy_threshold = old_buy

        if new_sell_threshold is not None:
            new_sell_threshold = _clamp(new_sell_threshold, -0.70, -0.20)
        else:
            new_sell_threshold = old_sell

        # Only apply if change is meaningful (> 0.05)
        buy_changed = abs(new_buy_threshold - old_buy) > 0.05
        sell_changed = abs(new_sell_threshold - old_sell) > 0.05

        if buy_changed or sell_changed:
            config.signal.buy_threshold = new_buy_threshold
            config.signal.sell_threshold = new_sell_threshold

            logger.info(
                "Threshold recalibrated: buy=%.2f→%.2f sell=%.2f→%.2f (based on %d trades)",
                old_buy, new_buy_threshold,
                old_sell, new_sell_threshold,
                len(records),
            )

            # Persist calibration
            self._calibration = {
                "buy_threshold": new_buy_threshold,
                "sell_threshold": new_sell_threshold,
                "based_on_trades": len(records),
                "calibrated_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            logger.debug(
                "Threshold recalibration: no significant change (buy=%.2f sell=%.2f).",
                new_buy_threshold, new_sell_threshold,
            )

    # ------------------------------------------------------------------
    # Private: persistence
    # ------------------------------------------------------------------

    def _load_all(self) -> None:
        """Load all JSON state files. Missing files are handled gracefully."""
        import json

        # keyword_weights.json
        try:
            if os.path.exists(self._weights_path):
                with open(self._weights_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._keyword_weights = data.get("weights", {})
                    self._word_trade_count = data.get("word_trade_count", {})
                logger.debug("Loaded %d keyword weights from %s", len(self._keyword_weights), self._weights_path)
        except Exception as exc:
            logger.warning("Could not load keyword_weights.json: %s", exc)
            self._keyword_weights = {}
            self._word_trade_count = {}

        # signal_accuracy.json
        try:
            if os.path.exists(self._accuracy_path):
                with open(self._accuracy_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._signal_records = data.get("records", [])
                logger.debug("Loaded %d signal records from %s", len(self._signal_records), self._accuracy_path)
        except Exception as exc:
            logger.warning("Could not load signal_accuracy.json: %s", exc)
            self._signal_records = []

        # calibration.json
        try:
            if os.path.exists(self._calibration_path):
                with open(self._calibration_path, "r", encoding="utf-8") as f:
                    self._calibration = json.load(f)
                # Apply saved thresholds to live config
                if "buy_threshold" in self._calibration:
                    config.signal.buy_threshold = self._calibration["buy_threshold"]
                    logger.debug("Restored buy_threshold=%.2f from calibration.json", config.signal.buy_threshold)
                if "sell_threshold" in self._calibration:
                    config.signal.sell_threshold = self._calibration["sell_threshold"]
                    logger.debug("Restored sell_threshold=%.2f from calibration.json", config.signal.sell_threshold)
        except Exception as exc:
            logger.warning("Could not load calibration.json: %s", exc)
            self._calibration = {}

    def _save_all(self) -> None:
        """Persist all in-memory state to JSON files."""
        # keyword_weights.json
        self._save_json(
            self._weights_path,
            {
                "weights": self._keyword_weights,
                "word_trade_count": self._word_trade_count,
            },
        )

        # signal_accuracy.json
        self._save_json(
            self._accuracy_path,
            {"records": self._signal_records},
        )

        # calibration.json (only if we have calibration data)
        if self._calibration:
            self._save_json(self._calibration_path, self._calibration)

    def _save_json(self, path: str, data: Any) -> None:
        """Atomic write: write to path+'.tmp', then os.replace to path."""
        import json

        tmp_path = path + ".tmp"
        try:
            # Ensure parent directory exists
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception as exc:
            logger.error("Failed to save %s: %s", path, exc)
            # Clean up temp file if it exists
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
