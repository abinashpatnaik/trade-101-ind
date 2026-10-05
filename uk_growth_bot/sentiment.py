"""News sentiment from free UK RSS feeds (keyword scored, in [-1, 1])."""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from typing import Dict, List

import feedparser

logger = logging.getLogger(__name__)

_WORDS = {
    **{w: 1.0 for w in ("surge", "soar", "record", "beat", "beats", "upgrade", "outperform",
                         "growth", "strong", "rally", "jumps", "boost", "raises", "wins")},
    **{w: 0.5 for w in ("up", "rise", "rises", "higher", "gain", "gains", "positive", "exceed")},
    **{w: -0.5 for w in ("down", "fall", "falls", "drop", "miss", "weak", "concern", "cut", "cuts")},
    **{w: -1.0 for w in ("crash", "plunge", "collapse", "loss", "downgrade", "underperform",
                          "warning", "slump", "tumble", "probe", "fraud", "lawsuit")},
}
_PATTERNS = [(re.compile(rf"\b{re.escape(w)}\b"), s) for w, s in _WORDS.items()]


def score_text(text: str) -> float:
    t = text.lower()
    hits = [s for p, s in _PATTERNS if p.search(t)]
    return sum(hits) / len(hits) if hits else 0.0


def _feeds(ticker: str, name: str) -> List[str]:
    q = urllib.parse.quote(f'"{name}" shares')
    return [
        f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=GB&lang=en-GB",
        f"https://news.google.com/rss/search?q={q}&hl=en-GB&gl=GB&ceid=GB:en",
    ]


def sentiment(ticker: str, name: str, max_age_days: int = 14) -> Dict[str, object]:
    cutoff = time.time() - max_age_days * 86400
    scores: List[float] = []
    headlines: List[str] = []
    for url in _feeds(ticker, name):
        try:
            for e in feedparser.parse(url).entries[:30]:
                pub = getattr(e, "published_parsed", None)
                if pub and time.mktime(pub) < cutoff:
                    continue
                title = getattr(e, "title", "").strip()
                if not title or title in headlines:
                    continue
                headlines.append(title)
                scores.append(score_text(f"{title} {getattr(e, 'summary', '')}"))
        except Exception as exc:
            logger.debug("Feed failed %s: %s", url, exc)
    nonzero = [s for s in scores if s != 0]
    avg = sum(nonzero) / len(nonzero) if nonzero else 0.0
    # Shrink toward 0 when only a handful of headlines mention the name.
    conf = min(1.0, len(nonzero) / 10)
    return {"score": round(avg * conf, 3), "n": len(headlines), "headlines": headlines[:3]}
