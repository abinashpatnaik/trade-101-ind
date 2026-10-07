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

SENTIMENT_TOP_N = 6


def run_research(closes: pd.DataFrame, held: Iterable[str], use_news: bool = True,
                 exclude: Iterable[str] = ()) -> Research:
    """*exclude*: tickers that can't be bought (wrong currency, not on the
    broker). They are left out of the rankings unless already held."""
    feats = latest_features(closes)
    held = set(held)
    feats = feats.drop(index=[t for t in set(exclude) - held if t in feats.index])
    core = [a.ticker for a in U.CORE_POOL + U.TWINS if a.ticker in feats.index]
    stocks = [a.ticker for a in U.SATELLITE_CANDIDATES if a.ticker in feats.index]

    mom: Dict[str, float] = {}
    for group in (core, stocks):
        if group:
            mom.update(momentum_scores(feats.loc[group]).to_dict())
    ml = predict_scores(feats)

    sent: Dict[str, float] = {}
    headlines: Dict[str, list] = {}
    if use_news:
        shortlist = sorted(stocks, key=lambda t: mom.get(t, -1), reverse=True)[:SENTIMENT_TOP_N]
        for t in dict.fromkeys(shortlist + [h for h in held if h in stocks]):
            s = sentiment(t, U.get(t).name)
            sent[t], headlines[t] = float(s["score"]), list(s["headlines"])

    scores = {t: compose(mom.get(t), ml.get(t), sent.get(t)) for t in feats.index}
    # Twins inherit their core fund's view: they track the same market.
    for a in U.CORE_POOL:
        if a.twin and a.ticker in scores:
            scores[a.twin] = scores[a.ticker]

    risk_on = above_trend(closes[U.REGIME_INDEX]) if U.REGIME_INDEX in closes else True
    return Research(feats, scores, mom, ml, sent, headlines, risk_on)


def regime_text(r: Research) -> str:
    return ("Risk-on: global equities above their 200-day average" if r.risk_on
            else "Risk-off: global equities below their 200-day average")


def top_candidates(r: Research, n: int = 5) -> list:
    stocks = [a.ticker for a in U.SATELLITE_CANDIDATES if a.ticker in r.scores]
    return sorted(stocks, key=lambda t: r.scores[t], reverse=True)[:n]
