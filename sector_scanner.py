#!/usr/bin/env python3
"""
sector_scanner.py
=================
Pre-market scanner for Sector Rotation.
Runs at 09:00 AM IST to scan the Nifty 50 universe for the best rising sectors
based on price momentum and news sentiment, selecting the top ML-approved stocks.
"""

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

import pandas as pd
import yfinance as yf

# Add the parent directory to sys.path so we can import agent modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sentiment_engine import _score_headline, _yahoo_rss_url
from ai_validator import AIValidator
from decision_engine import DecisionEngine
from config import config

ACTIVE_MARKET = os.getenv("TRADING_MARKET", "IN").upper()
from trend_engine import TrendEngine, TrendSignal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Detect path logic matching config.py
_IN_DOCKER = os.path.exists("/app")
DATA_DIR = "/app/data" if _IN_DOCKER else "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

_UNIVERSE_FILENAME = "us_universe.json" if ACTIVE_MARKET == "US" else "nse_universe.json"
UNIVERSE_FILE = os.path.join(os.path.dirname(__file__), _UNIVERSE_FILENAME)
TARGETS_FILE = os.path.join(DATA_DIR, f"daily_targets_{ACTIVE_MARKET}.json")

def fetch_rss_sentiment(symbol: str) -> float:
    """Fetch Yahoo Finance RSS feed and calculate average sentiment for a symbol."""
    yf_sym = symbol.replace(".", "-") if ACTIVE_MARKET == "US" else symbol
    url = _yahoo_rss_url(yf_sym)
    try:
        import feedparser
        parsed = feedparser.parse(url)
        if not parsed.entries:
            return 0.0
        scores = []
        for entry in parsed.entries[:5]:  # Top 5 news
            scores.append(_score_headline(entry.title))
        return sum(scores) / len(scores)
    except Exception as e:
        logger.debug(f"Error fetching RSS for {symbol}: {e}")
        return 0.0

def select_rising_sector_candidates(
    stock_metrics: Dict[str, Dict], top_n_sectors: int = 2
) -> Tuple[List[str], List[str]]:
    """Group stocks by sector, rank sectors by avg momentum+sentiment, and
    return (top_sectors, candidate_stocks) -- the candidates being stocks
    in a top sector with positive momentum and non-negative sentiment.

    Before get_us_dynamic_universe carried real GICS sectors, every US
    stock shared one placeholder sector ("Dynamic US"), so this always
    degenerated to a single fake "top sector" containing the entire
    universe -- the sector-rotation filtering did nothing. With real
    sectors this actually concentrates on the 2 genuinely strongest ones.
    """
    sector_metrics: Dict[str, Dict] = {}
    for m in stock_metrics.values():
        sector = m["sector"]
        agg = sector_metrics.setdefault(
            sector, {"total_momentum": 0.0, "total_sentiment": 0.0, "count": 0}
        )
        agg["total_momentum"] += m["momentum"]
        agg["total_sentiment"] += m["sentiment"]
        agg["count"] += 1

    sector_scores = []
    for sector, metrics in sector_metrics.items():
        if metrics["count"] >= 2:  # Must have at least 2 stocks in universe sector
            avg_mom = metrics["total_momentum"] / metrics["count"]
            avg_sent = metrics["total_sentiment"] / metrics["count"]
            combined_score = (avg_mom * 100) + avg_sent  # Weight momentum highly
            sector_scores.append((sector, combined_score))

    sector_scores.sort(key=lambda x: x[1], reverse=True)
    top_sectors = [s[0] for s in sector_scores[:top_n_sectors]]

    candidate_stocks = [
        t for t, m in stock_metrics.items()
        if m["sector"] in top_sectors and m["momentum"] > 0 and m["sentiment"] >= 0
    ]
    return top_sectors, candidate_stocks


def run_scanner():
    logger.info("Starting Pre-Market Sector Scanner...")
    
    if ACTIVE_MARKET == "IN":
        from market_screener import get_dynamic_universe
        yf_tickers = get_dynamic_universe(50)
        tickers = yf_tickers # In India, the ticker IS the YF ticker (e.g. RELIANCE.NS)
        universe_map = {t: "Dynamic" for t in tickers}
        logger.info(f"Loaded {len(tickers)} dynamic tickers from NSE/BSE scanner.")
    else:
        from market_screener import get_us_dynamic_universe
        # (yfinance ticker, real GICS sector) pairs -- see get_us_dynamic_universe's
        # docstring for why this used to be a flat list tagged with one fake
        # "Dynamic US" sector for every stock, which made the "top rising
        # sectors" grouping below a no-op.
        dynamic_universe = get_us_dynamic_universe(50)
        yf_tickers = [t for t, _ in dynamic_universe]
        sector_by_yf_ticker = dict(dynamic_universe)
        # Convert yfinance tickers back to standard tickers (e.g. BRK-B -> BRK.B)
        tickers = [t.replace("-", ".") for t in yf_tickers]
        universe_map = {
            t.replace("-", "."): sector_by_yf_ticker.get(t, "Unknown")
            for t in yf_tickers
        }
        logger.info(f"Loaded {len(tickers)} dynamic tickers from US scanner.")
    
    # 1. Bulk Download 1 Month of Data
    logger.info("Downloading 1-month OHLCV data for momentum calculation...")

    df_all = yf.download(
        " ".join(yf_tickers), 
        period="3mo", 
        interval="1d", 
        group_by="ticker", 
        progress=False,
        threads=True
    )
    
    # 2. Scrape News Sentiment Concurrently
    logger.info("Scraping Yahoo Finance RSS news for all tickers...")
    sentiment_scores = {}
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_sym = {executor.submit(fetch_rss_sentiment, t): t for t in tickers}
        for future in as_completed(future_to_sym):
            sym = future_to_sym[future]
            try:
                sentiment_scores[sym] = future.result()
            except Exception:
                sentiment_scores[sym] = 0.0
                
    # 3. Calculate Momentum per stock (sector aggregation happens in
    # select_rising_sector_candidates once stock_metrics is built)
    stock_metrics = {}

    for t in tickers:
        yf_t = t.replace(".", "-") if ACTIVE_MARKET == "US" else t
        momentum = 0.0
        if isinstance(df_all.columns, pd.MultiIndex) and yf_t in df_all.columns.levels[0]:
            try:
                close_prices = df_all[yf_t]["Close"].dropna()
                if len(close_prices) >= 10:
                    start_price = float(close_prices.iloc[0])
                    end_price = float(close_prices.iloc[-1])
                    if start_price > 0:
                        momentum = (end_price / start_price) - 1.0
            except Exception:
                pass
                
        sentiment = sentiment_scores.get(t, 0.0)
        sector = universe_map[t]
        
        stock_metrics[t] = {
            "momentum": momentum,
            "sentiment": sentiment,
            "sector": sector
        }

    # 4. Find top rising sectors, then filter candidates within them
    top_sectors, candidate_stocks = select_rising_sector_candidates(stock_metrics)
    logger.info(f"Top 2 Rising Sectors identified: {top_sectors}")
    logger.info(f"Found {len(candidate_stocks)} candidate stocks in rising sectors with positive momentum/news.")
    
    # 5. ML Validation
    logger.info("Validating candidates through XGBoost ML Model...")
    
    ai_validator = AIValidator()
    decision_engine = DecisionEngine()
    if ai_validator.model_day is None and ai_validator.model_swing is None:
        logger.warning("ML model not found or disabled. Falling back to non-ML selection.")
        # Fallback: Just take the top 15 candidate stocks by combined momentum and sentiment
        candidate_stocks.sort(key=lambda x: stock_metrics[x]["momentum"] + stock_metrics[x]["sentiment"], reverse=True)
        
        final_symbols = []
        for t in candidate_stocks[:15]:
            final_symbols.append(t.replace(".", "-") if ACTIVE_MARKET == "US" else t)
                
        logger.info(f"Fallback selected {len(final_symbols)} final targets.")
        with open(TARGETS_FILE, "w") as f:
            json.dump(final_symbols, f, indent=4)
        return
        
    trend_engine = TrendEngine()
    
    approved_targets = []
    
    for symbol in candidate_stocks:
        yf_t = symbol.replace(".", "-") if ACTIVE_MARKET == "US" else symbol
        df_symbol = None
        if isinstance(df_all.columns, pd.MultiIndex):
            if yf_t in df_all.columns.levels[0]:
                df_symbol = df_all[yf_t].dropna()
        else:
            df_symbol = df_all.dropna()
            
        if df_symbol is None or df_symbol.empty:
            continue
            
        try:
            # Call trend engine with the downloaded data
            signal = trend_engine.analyse(yf_t, df_symbol)
        except Exception as e:
            logger.debug(f"TrendEngine failed for {symbol}: {e}")
            continue
            
        if signal is None or signal.overall_trend <= 0:
            continue
            
        try:
            # Go through get_ml_confidence() rather than rebuilding the
            # feature frame and calling model_swing.predict_proba() directly
            # -- that duplicate path bypassed the isotonic calibrator
            # entirely, so this scanner's "confidence" and the live trader's
            # would silently drift apart (uncalibrated vs calibrated) even
            # though both read from the same swing model.
            prob_success = ai_validator.get_ml_confidence(
                signal, stock_metrics[symbol]["sentiment"], mode="swing"
            )

            # Was a hardcoded absolute 0.55 ("raised from 0.40 for genuine
            # signal only") -- the same class of bug as the old absolute
            # 0.50 BUY-threshold floor. Once confidence is honestly
            # calibrated (#63), it rarely clears an absolute bar like this
            # at all: every candidate came back "ML: 0.0%" and the fallback
            # (plain momentum+sentiment, no ML filtering) fired on every
            # run since calibration landed -- confirmed live 2026-10-08.
            # Use the same base-rate-relative floor decision_engine already
            # applies to the live BUY gate, so "genuine ML signal" means the
            # same thing here as it does everywhere else.
            floor = decision_engine.get_relative_threshold_floor(is_swing=True)
            if prob_success >= floor:
                approved_targets.append({
                    "symbol": yf_t,
                    "sector": stock_metrics[symbol]["sector"],
                    "ml_confidence": float(prob_success),
                    "momentum": float(stock_metrics[symbol]["momentum"]),
                    "sentiment": float(stock_metrics[symbol]["sentiment"])
                })
        except Exception as e:
            logger.debug(f"ML Prediction failed for {symbol}: {e}")
            
    approved_targets.sort(key=lambda x: x["ml_confidence"], reverse=True)
    final_targets = approved_targets[:15]
    
    if not final_targets:
        logger.warning("ML strict validation yielded 0 targets. Falling back to top 15 by momentum/sentiment.")
        candidate_stocks.sort(key=lambda x: stock_metrics[x]["momentum"] + stock_metrics[x]["sentiment"], reverse=True)
        
        final_targets = []
        for s in candidate_stocks[:15]:
            if ACTIVE_MARKET == "US":
                yf_t = s.replace(".", "-")
            else:
                yf_t = f"{s.replace('.', '-')}.NS"
            final_targets.append({
                "symbol": yf_t,
                "sector": stock_metrics[s]["sector"],
                "ml_confidence": 0.0
            })
    
    logger.info(f"Selected {len(final_targets)} final targets for today's trading session.")
    for t in final_targets:
        logger.info(f"  -> {t['symbol']} ({t['sector']}) | ML: {t['ml_confidence']*100:.1f}%")
        
    with open(TARGETS_FILE, "w") as f:
        json.dump([t["symbol"] for t in final_targets], f, indent=4)
        
    logger.info(f"Saved daily targets to {TARGETS_FILE}")

if __name__ == "__main__":
    run_scanner()
