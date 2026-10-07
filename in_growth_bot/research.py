"""Market research: momentum, validated ML and news sentiment for the universe."""

from __future__ import annotations

import logging
from typing import Dict, Iterable

import pandas as pd

from . import universe as U
from .features import above_trend, latest_features, momentum_scores
from .ml_model import predict_scores
from .planner import Research, compose
from .sentiment import sentiment

logger = logging.getLogger(__name__)

SENTIMENT_TOP_N = 10


def run_research(closes: pd.DataFrame, held: Iterable[str], use_news: bool = True,
                 exclude: Iterable[str] = ()) -> Research:
    """Scores the investable pool plus anything held. *exclude*: tickers that
    can't be bought (e.g. not on Zerodha); left out unless already held."""
    held = set(held)
    pool = [a.ticker for a in U.pool()]
    cols = [t for t in dict.fromkeys(pool + sorted(held)) if t in closes and U.ALL.get(t)]
    feats = latest_features(closes[cols]) if cols else pd.DataFrame()
    feats = feats.drop(index=[t for t in set(exclude) - held if t in feats.index])

    mom: Dict[str, float] = momentum_scores(feats).to_dict() if not feats.empty else {}
    ml = predict_scores(feats)

    sent: Dict[str, float] = {}
    headlines: Dict[str, list] = {}
    if use_news:
        shortlist = sorted(feats.index, key=lambda t: mom.get(t, -1), reverse=True)[:SENTIMENT_TOP_N]
        for t in dict.fromkeys(list(shortlist) + [h for h in held if h in feats.index]):
            s = sentiment(t, U.get(t).name)
            sent[t], headlines[t] = float(s["score"]), list(s["headlines"])

    scores = {t: compose(mom.get(t), ml.get(t), sent.get(t)) for t in feats.index}
    risk_on = above_trend(closes[U.REGIME_INDEX]) if U.REGIME_INDEX in closes else True
    return Research(feats, scores, mom, ml, sent, headlines, risk_on)


def regime_text(r: Research) -> str:
    return ("Risk-on: Nifty 50 above its 200-day average" if r.risk_on
            else "Risk-off: Nifty 50 below its 200-day average")


def top_candidates(r: Research, n: int = 8) -> list:
    return sorted(r.scores, key=lambda t: r.scores[t], reverse=True)[:n]
