"""
sentiment_engine.py
===================
Custom sentiment engine using free data sources only.
No paid API keys required.

Sources:
  1. RSS news feeds (BBC, Reuters, Yahoo Finance)
  2. Price momentum from yfinance intraday data
  3. Own trade history from trades.csv
"""

from __future__ import annotations

import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import feedparser
import pandas as pd
import yfinance as yf

from config import config

logger = logging.getLogger(__name__)

ACTIVE_MARKET = os.getenv("TRADING_MARKET", "IN").upper()

# ---------------------------------------------------------------------------
# Keyword scoring dictionaries
# ---------------------------------------------------------------------------

_STRONG_POSITIVE: List[str] = [
    "surge", "soar", "record", "beat", "upgrade", "buy", "outperform",
    "profit", "growth", "strong", "rally", "gain", "rise", "jumps", "boost",
]
_MOD_POSITIVE: List[str] = [
    "up", "increase", "positive", "higher", "above", "exceed", "good", "well",
]
_NEUTRAL: List[str] = [
    "holds", "steady", "flat", "unchanged", "mixed",
]
_MOD_NEGATIVE: List[str] = [
    "down", "fall", "drop", "miss", "below", "weak", "concern", "cut",
]
_STRONG_NEGATIVE: List[str] = [
    "crash", "plunge", "collapse", "loss", "downgrade", "sell", "underperform",
    "warning", "risk", "decline", "slump", "tumble",
]

_KEYWORD_SCORES: Dict[str, float] = {}
for _w in _STRONG_POSITIVE:
    _KEYWORD_SCORES[_w] = 1.0
for _w in _MOD_POSITIVE:
    _KEYWORD_SCORES[_w] = 0.5
for _w in _NEUTRAL:
    _KEYWORD_SCORES[_w] = 0.0
for _w in _MOD_NEGATIVE:
    _KEYWORD_SCORES[_w] = -0.5
for _w in _STRONG_NEGATIVE:
    _KEYWORD_SCORES[_w] = -1.0

# ---------------------------------------------------------------------------
# RSS feed URLs
# ---------------------------------------------------------------------------

_RSS_FEEDS_GENERIC = [
    "https://www.moneycontrol.com/rss/latestnews.xml",
    "https://economictimes.indiatimes.com/rssfeedsdefault.cms",
    "https://www.livemint.com/rss/companies",
]


def _yahoo_rss_url(symbol: str) -> str:
    """Return the Yahoo Finance RSS URL for an NSE ticker."""
    yf_sym = symbol.upper()
    if ACTIVE_MARKET == "US":
        yf_sym = yf_sym.replace(".", "-")
    elif not (yf_sym.endswith(".NS") or yf_sym.endswith(".BO")):
        yf_sym = yf_sym + ".NS"
    return f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={yf_sym}"


def _google_news_rss_url(query: str) -> str:
    """Return Google News RSS search URL for a query."""
    import urllib.parse
    encoded_query = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"


def _yfinance_symbol(symbol: str) -> str:
    """Convert a ticker to yfinance format (append '.NS' if not US)."""
    sym = symbol.upper()
    if ACTIVE_MARKET == "US":
        return sym.replace(".", "-")
    if not (sym.endswith(".NS") or sym.endswith(".BO")):
        sym = sym + ".NS"
    return sym


# ---------------------------------------------------------------------------
# Score a single headline string
# ---------------------------------------------------------------------------

def _score_headline(text: str) -> float:
    """
    Score a headline using the keyword dictionary.

    Returns a float in [-1.0, 1.0].  If no keyword matches, returns 0.0.
    """
    text_lower = text.lower()
    scores: List[float] = []
    for keyword, score in _KEYWORD_SCORES.items():
        # Use word-boundary style check: the keyword must appear as a
        # standalone word (surrounded by non-alphanumeric chars or boundaries).
        # Simple check: split on whitespace/punctuation and see if any token
        # matches.  This avoids partial matches like "upped" → "up".
        import re
        if re.search(r"\b" + re.escape(keyword) + r"\b", text_lower):
            scores.append(score)
    if not scores:
        return 0.0
    # Use the most extreme score if multiple keywords found (captures dominant tone).
    # If mixed, average them.
    if len(scores) == 1:
        return scores[0]
    return sum(scores) / len(scores)


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))


class SentimentEngine:
    """
    Multi-signal sentiment engine for traded stocks.

    Combines three free-data signals:
      1. RSS news sentiment     (weight 0.5)
      2. Price momentum         (weight 0.3)
      3. Own trade history      (weight 0.2)

    All signals fall back gracefully to 0.0 on any error.

    Usage
    -----
    >>> engine = SentimentEngine()
    >>> score = engine.get_sentiment('BARC')   # float in [-1, 1]
    >>> headlines = engine.get_news_headlines('BARC', limit=5)
    """

    def __init__(self) -> None:
        # {symbol -> (score, monotonic_timestamp)}
        self._news_cache: Dict[str, Tuple[float, float]] = {}
        self._momentum_cache: Dict[str, Tuple[float, float]] = {}

        # Computed once per session from trades.csv
        self._trade_history_score: Dict[str, float] = {}
        self._trade_history_loaded: bool = False

        self._news_cache_ttl: float = 30 * 60       # 30 minutes
        self._momentum_cache_ttl: float = 5 * 60    # 5 minutes

        # Learning engine integration — set by agent.py after init
        self._learning_engine: Optional[Any] = None

        # Store recent headlines per symbol for learning feedback
        self._last_headlines: Dict[str, List[str]] = {}

        # Map NSE ticker → common company name variants for headline matching
        self._company_names: Dict[str, List[str]] = {
            "RELIANCE.NS": ["Reliance", "Reliance Industries", "RIL"],
            "TCS.NS": ["TCS", "Tata Consultancy Services", "Tata Consultancy"],
            "HDFCBANK.NS": ["HDFC Bank", "HDFC"],
            "INFY.NS": ["Infosys", "Infosys Technologies"],
            "ICICIBANK.NS": ["ICICI Bank", "ICICI"],
            "HINDUNILVR.NS": ["Hindustan Unilever", "HUL", "Unilever India"],
            "ITC.NS": ["ITC", "ITC Limited"],
            "SBIN.NS": ["SBI", "State Bank of India", "State Bank"],
            "BHARTIARTL.NS": ["Bharti Airtel", "Airtel"],
            "AXISBANK.NS": ["Axis Bank", "Axis"],
            "LT.NS": ["Larsen & Toubro", "L&T", "Larsen and Toubro"],
            "KOTAKBANK.NS": ["Kotak Mahindra Bank", "Kotak Bank", "Kotak"],
            "SUNPHARMA.NS": ["Sun Pharmaceutical", "Sun Pharma"],
            "M&M.NS": ["Mahindra & Mahindra", "M&M", "Mahindra"],
            "ULTRACEMCO.NS": ["UltraTech Cement", "UltraTech"],
            "HCLTECH.NS": ["HCL Technologies", "HCL Tech", "HCL"],
            "LTIM.NS": ["LTIMindtree", "LTIM"],
            "ASIANPAINT.NS": ["Asian Paints"],
            "BAJFINANCE.NS": ["Bajaj Finance"],
            "MARUTI.NS": ["Maruti Suzuki", "Maruti"],
        }

    # ------------------------------------------------------------------
    # Learning engine integration
    # ------------------------------------------------------------------

    def set_learning_engine(self, engine) -> None:
        """Attach a LearningEngine instance to blend learned weights into scoring."""
        self._learning_engine = engine

    def get_last_headlines(self, symbol: str) -> List[str]:
        """Return the most recently cached headlines for *symbol* (for learning feedback)."""
        return self._last_headlines.get(symbol.upper(), [])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_sentiment(self, symbol: str) -> float:
        """
        Return a combined sentiment score in [-1.0, 1.0] for *symbol*.

        Weights:
          news      0.5
          momentum  0.3
          history   0.2

        Falls back to 0.0 per signal on any error.
        """
        symbol = symbol.upper()

        news = 0.0
        momentum = 0.0
        history = 0.0

        try:
            news = self._get_news_sentiment(symbol)
        except Exception as exc:
            logger.warning("News sentiment failed for %s: %s", symbol, exc)

        try:
            momentum = self._get_momentum_sentiment(symbol)
        except Exception as exc:
            logger.warning("Momentum sentiment failed for %s: %s", symbol, exc)

        try:
            history = self._get_trade_history_sentiment(symbol)
        except Exception as exc:
            logger.warning("Trade history sentiment failed for %s: %s", symbol, exc)

        combined = _clamp(0.5 * news + 0.3 * momentum + 0.2 * history, -1.0, 1.0)

        logger.info(
            "Sentiment %s: news=%.2f momentum=%.2f history=%.2f combined=%.2f",
            symbol, news, momentum, history, combined,
        )
        return combined

    def get_news_headlines(self, symbol: str, limit: int = 5) -> List[str]:
        """
        Return recent news headlines mentioning *symbol* or its company name.

        Fetches Yahoo Finance RSS for the symbol first, then generic feeds.
        No caching applied — always live.

        Parameters
        ----------
        symbol:
            Bare LSE ticker, e.g. ``'BARC'``.
        limit:
            Maximum headlines to return.

        Returns
        -------
        list of str
            Headline strings, newest first.  Empty list on failure.
        """
        symbol = symbol.upper()
        headlines: List[str] = []
        search_terms = self._search_terms(symbol)

        primary_query = search_terms[1] if len(search_terms) > 1 else symbol
        # Yahoo Finance and Google News search feeds first (most relevant)
        urls = [
            _yahoo_rss_url(symbol),
            _google_news_rss_url(f"{primary_query} stock"),
            _google_news_rss_url(primary_query)
        ] + _RSS_FEEDS_GENERIC

        for url in urls:
            if len(headlines) >= limit:
                break
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries:
                    if len(headlines) >= limit:
                        break
                    title = getattr(entry, "title", "").strip()
                    if not title:
                        continue
                    # For Yahoo and Google News search feeds (symbol-specific) include all headlines;
                    # for generic feeds, filter by relevance.
                    is_specific = (url == _yahoo_rss_url(symbol) or "news.google.com/rss/search" in url)
                    if is_specific or self._headline_mentions(title, search_terms):
                        headlines.append(title)
            except Exception as exc:
                logger.debug("Failed to fetch headlines from %s: %s", url, exc)

        return headlines[:limit]

    def get_bulk_sentiments(self, symbols: List[str]) -> Dict[str, float]:
        """
        Fetch sentiment for a list of symbols.

        Returns a dict mapping each symbol to its sentiment score.
        """
        return {sym: self.get_sentiment(sym) for sym in symbols}

    def invalidate_cache(self, symbol: Optional[str] = None) -> None:
        """
        Invalidate news and momentum caches.

        Parameters
        ----------
        symbol:
            If provided, remove only this symbol's entry.
            If None, clear all caches.
        """
        if symbol is None:
            self._news_cache.clear()
            self._momentum_cache.clear()
            logger.debug("All sentiment caches cleared.")
        else:
            sym = symbol.upper()
            self._news_cache.pop(sym, None)
            self._momentum_cache.pop(sym, None)
            logger.debug("Sentiment cache invalidated for %s.", sym)

    # ------------------------------------------------------------------
    # Signal 1: News sentiment
    # ------------------------------------------------------------------

    def _get_news_sentiment(self, symbol: str) -> float:
        """
        Fetch and score RSS news headlines for *symbol*.

        Returns a score in [-1.0, 1.0], or 0.0 if no relevant articles found.
        Results cached for 30 minutes.
        """
        # Cache check
        if symbol in self._news_cache:
            score, ts = self._news_cache[symbol]
            if (time.monotonic() - ts) < self._news_cache_ttl:
                logger.debug("News cache hit for %s: %.4f", symbol, score)
                return score

        search_terms = self._search_terms(symbol)
        now_utc = datetime.now(timezone.utc)
        cutoff_ts = now_utc.timestamp() - 86400  # 24 hours ago

        scores: List[float] = []

        primary_query = search_terms[1] if len(search_terms) > 1 else symbol
        # Try Yahoo Finance and Google News RSS first (symbol-specific), then generic feeds
        yahoo_url = _yahoo_rss_url(symbol)
        feed_urls = [
            yahoo_url,
            _google_news_rss_url(f"{primary_query} stock"),
            _google_news_rss_url(primary_query)
        ] + _RSS_FEEDS_GENERIC

        for url in feed_urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries:
                    title = getattr(entry, "title", "").strip()
                    summary = getattr(entry, "summary", "").strip()
                    text = f"{title} {summary}"

                    # Filter by recency
                    published = getattr(entry, "published_parsed", None)
                    if published is not None:
                        entry_ts = time.mktime(published)
                        if entry_ts < cutoff_ts:
                            continue

                    # Filter by relevance (except Yahoo/Google News search — already symbol-specific)
                    is_specific = (url == yahoo_url or "news.google.com/rss/search" in url)
                    if not is_specific and not self._headline_mentions(text, search_terms):
                        continue

                    headline_score = _score_headline(text)
                    scores.append(headline_score)
                    # Store title for later learning feedback
                    if title:
                        stored = self._last_headlines.setdefault(symbol, [])
                        if title not in stored:
                            stored.append(title)

            except Exception as exc:
                logger.debug("RSS fetch failed for %s (%s): %s", symbol, url, exc)
                continue

        # Keep only the most recent 10 headlines per symbol
        self._last_headlines[symbol] = self._last_headlines.get(symbol, [])[:10]

        if not scores:
            static_score = 0.0
        else:
            static_score = _clamp(sum(scores) / len(scores), -1.0, 1.0)

        # Blend static keyword score with learned weights (if engine attached)
        result = static_score
        if self._learning_engine is not None:
            # Combine all headline text for the learned score
            all_text = " ".join(self._last_headlines.get(symbol, []))
            learned_score = self._learning_engine.get_keyword_score(all_text)
            # Learned weight ramps from 0 up to 0.6 over the first 50 trades
            n_trades = len(self._learning_engine._signal_records)
            learned_weight = min(0.6, n_trades / 50 * 0.6)
            static_weight = 1.0 - learned_weight
            result = _clamp(
                static_weight * static_score + learned_weight * learned_score,
                -1.0, 1.0,
            )
            logger.debug(
                "News sentiment %s: static=%.4f learned=%.4f blend(%.0f%%/%.0f%%)=%.4f",
                symbol, static_score, learned_score,
                static_weight * 100, learned_weight * 100, result,
            )

        self._news_cache[symbol] = (result, time.monotonic())
        logger.debug("News sentiment for %s: %.4f (%d articles)", symbol, result, len(scores))
        return result

    # ------------------------------------------------------------------
    # Signal 2: Price momentum sentiment
    # ------------------------------------------------------------------

    def _get_momentum_sentiment(self, symbol: str) -> float:
        """
        Compute momentum sentiment from yfinance intraday 5-minute bars.

        Formula:
          roc_score  = clamp(roc * 20, -1, 1)
          vol_boost  = min(volume_ratio - 1, 1) * 0.3
          momentum   = clamp(roc_score + vol_boost, -1, 1)

        Returns 0.0 on error or insufficient data.
        Cached for 5 minutes.
        """
        # Cache check
        if symbol in self._momentum_cache:
            score, ts = self._momentum_cache[symbol]
            if (time.monotonic() - ts) < self._momentum_cache_ttl:
                logger.debug("Momentum cache hit for %s: %.4f", symbol, score)
                return score

        yf_sym = _yfinance_symbol(symbol)
        result = 0.0

        try:
            ticker = yf.Ticker(yf_sym)
            hist = ticker.history(period="5d", interval="5m")

            if hist is None or hist.empty or len(hist) < 6:
                logger.debug(
                    "Insufficient intraday data for %s (%s rows)", symbol, 0 if hist is None else len(hist)
                )
                self._momentum_cache[symbol] = (0.0, time.monotonic())
                return 0.0

            closes = hist["Close"].dropna()
            volumes = hist["Volume"].dropna()

            if len(closes) < 6:
                self._momentum_cache[symbol] = (0.0, time.monotonic())
                return 0.0

            current_price = float(closes.iloc[-1])
            price_5_ago = float(closes.iloc[-6])

            if price_5_ago == 0:
                self._momentum_cache[symbol] = (0.0, time.monotonic())
                return 0.0

            roc = (current_price - price_5_ago) / price_5_ago

            # Volume ratio: current bar vs 20-bar average
            current_volume = float(volumes.iloc[-1])
            avg_volume_20 = float(volumes.iloc[-min(20, len(volumes)):].mean()) if len(volumes) >= 2 else 1.0
            volume_ratio = (current_volume / avg_volume_20) if avg_volume_20 > 0 else 1.0

            roc_score = _clamp(roc * 20, -1.0, 1.0)
            vol_boost = min(volume_ratio - 1.0, 1.0) * 0.3
            result = _clamp(roc_score + vol_boost, -1.0, 1.0)

            logger.debug(
                "Momentum %s: roc=%.4f roc_score=%.4f vol_ratio=%.2f vol_boost=%.4f momentum=%.4f",
                symbol, roc, roc_score, volume_ratio, vol_boost, result,
            )

        except Exception as exc:
            logger.warning("Momentum calculation failed for %s: %s", symbol, exc)
            result = 0.0

        self._momentum_cache[symbol] = (result, time.monotonic())
        return result

    # ------------------------------------------------------------------
    # Signal 3: Trade history sentiment
    # ------------------------------------------------------------------

    def _get_trade_history_sentiment(self, symbol: str) -> float:
        """
        Compute a 'personal alpha' score from the agent's own trade history.

        Win rate rules (after recency weighting):
          win_rate > 0.6  →  +0.5
          win_rate < 0.4  →  -0.5
          < 3 trades      →   0.0
          0.4–0.6         →   0.0  (inconclusive)

        Recency weighting: exponential decay, half-life = 5 trades.
        Scores cached for the lifetime of this SentimentEngine instance.
        """
        if not self._trade_history_loaded:
            self._load_trade_history()

        return self._trade_history_score.get(symbol, 0.0)

    def _load_trade_history(self) -> None:
        """
        Load and score all symbols from trades.csv.  Called once per session.
        """
        self._trade_history_loaded = True  # Set early to prevent re-entry on error

        trades_csv = config.agent.trades_csv
        if not os.path.exists(trades_csv):
            logger.debug("trades.csv not found at %s — history sentiment unavailable.", trades_csv)
            return

        try:
            df = pd.read_csv(trades_csv)
        except Exception as exc:
            logger.warning("Failed to read trades.csv: %s", exc)
            return

        # Normalise column names to lowercase
        df.columns = [c.strip().lower() for c in df.columns]

        # We need at minimum: symbol, action/side, pnl or outcome
        # Acceptable column names for the trade side: action, side, direction, type
        side_col = next(
            (c for c in df.columns if c in ("action", "side", "direction", "type", "trade_type")),
            None,
        )
        symbol_col = next(
            (c for c in df.columns if c in ("symbol", "ticker", "stock")),
            None,
        )
        pnl_col = next(
            (c for c in df.columns if c in ("pnl", "p&l", "profit_loss", "realized_pnl", "net_pnl")),
            None,
        )

        if symbol_col is None:
            logger.debug("trades.csv has no recognisable symbol column — columns: %s", list(df.columns))
            return

        # Filter to closed trades (SELL side)
        if side_col is not None:
            sells = df[df[side_col].str.upper().isin(["SELL", "CLOSE", "SHORT"])].copy()
        else:
            # No side column — treat all rows as closed trades
            sells = df.copy()

        if sells.empty:
            logger.debug("No SELL trades found in trades.csv.")
            return

        # Determine win/loss per trade
        if pnl_col is not None:
            sells["_win"] = pd.to_numeric(sells[pnl_col], errors="coerce").fillna(0) > 0
        else:
            logger.debug("No P&L column found in trades.csv — cannot compute win rate.")
            return

        # Half-life = 5 trades → decay factor per trade
        half_life = 5
        decay = math.log(2) / half_life  # λ = ln2 / half_life

        for sym, group in sells.groupby(symbol_col):
            sym_str = str(sym).upper()
            n = len(group)
            if n < 3:
                self._trade_history_score[sym_str] = 0.0
                continue

            # Sort by row order (proxy for chronological order if no date col)
            date_col = next(
                (c for c in group.columns if c in ("date", "timestamp", "time", "datetime", "trade_date")),
                None,
            )
            if date_col:
                try:
                    group = group.sort_values(date_col)
                except Exception:
                    pass  # Keep original order

            wins_arr = group["_win"].values.astype(float)
            # Recency weights: most recent trade has weight 1, older ones decay
            weights = [math.exp(-decay * (n - 1 - i)) for i in range(n)]
            total_weight = sum(weights)
            weighted_wins = sum(w * wl for w, wl in zip(weights, wins_arr))
            win_rate = weighted_wins / total_weight if total_weight > 0 else 0.5

            if win_rate > 0.6:
                score = 0.5
            elif win_rate < 0.4:
                score = -0.5
            else:
                score = 0.0

            self._trade_history_score[sym_str] = score
            logger.debug(
                "Trade history %s: n=%d win_rate=%.3f score=%.1f",
                sym_str, n, win_rate, score,
            )

        logger.info(
            "Trade history loaded: %d symbols scored from %s",
            len(self._trade_history_score), trades_csv,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _search_terms(self, symbol: str) -> List[str]:
        """
        Return a list of strings to search for in headlines for *symbol*.
        Includes the bare ticker and all known company name variants.
        """
        terms = [symbol.upper()]
        terms += self._company_names.get(symbol.upper(), [])
        return terms

    @staticmethod
    def _headline_mentions(text: str, search_terms: List[str]) -> bool:
        """Return True if *text* contains any of the *search_terms* (case-insensitive)."""
        text_lower = text.lower()
        return any(term.lower() in text_lower for term in search_terms)
