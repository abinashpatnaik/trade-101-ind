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

UNIVERSE_FILE = os.path.join(os.path.dirname(__file__), "nse_universe.json")
TARGETS_FILE = os.path.join(DATA_DIR, "daily_targets.json")

def fetch_rss_sentiment(symbol: str) -> float:
    """Fetch Yahoo Finance RSS feed and calculate average sentiment for a symbol."""
    if ACTIVE_MARKET == "US":
        yf_sym = symbol.replace(".", "-")
    else:
        yf_sym = f"{symbol.replace('.', '-')}.NS"
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

def run_scanner():
    logger.info("Starting Pre-Market Sector Scanner...")
    
    if not os.path.exists(UNIVERSE_FILE):
        logger.error(f"Universe file {UNIVERSE_FILE} not found!")
        return
        
    with open(UNIVERSE_FILE, "r") as f:
        universe_map = json.load(f)
        
    tickers = list(universe_map.keys())
    logger.info(f"Loaded {len(tickers)} tickers from the Nifty 50 universe.")
    
    # 1. Bulk Download 1 Month of Data
    logger.info("Downloading 1-month OHLCV data for momentum calculation...")
    if ACTIVE_MARKET == "US":
        yf_tickers = [t.replace(".", "-") for t in tickers]
    else:
        yf_tickers = [f"{t.replace('.', '-')}.NS" for t in tickers]
    df_all = yf.download(
        " ".join(yf_tickers), 
        period="1mo", 
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
                
    # 3. Calculate Momentum and Aggregate by Sector
    stock_metrics = {}
    sector_metrics = {}
    
    for t in tickers:
        if ACTIVE_MARKET == "US":
            yf_t = t.replace(".", "-")
        else:
            yf_t = f"{t.replace('.', '-')}.NS"
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
        
        if sector not in sector_metrics:
            sector_metrics[sector] = {"total_momentum": 0, "total_sentiment": 0, "count": 0}
            
        sector_metrics[sector]["total_momentum"] += momentum
        sector_metrics[sector]["total_sentiment"] += sentiment
        sector_metrics[sector]["count"] += 1
        
    # Find Top Sectors (Combining average momentum + average sentiment)
    sector_scores = []
    for sector, metrics in sector_metrics.items():
        if metrics["count"] >= 2: # Must have at least 2 stocks in universe sector
            avg_mom = metrics["total_momentum"] / metrics["count"]
            avg_sent = metrics["total_sentiment"] / metrics["count"]
            combined_score = (avg_mom * 100) + avg_sent  # Weight momentum highly
            sector_scores.append((sector, combined_score))
            
    sector_scores.sort(key=lambda x: x[1], reverse=True)
    top_sectors = [s[0] for s in sector_scores[:2]]
    logger.info(f"Top 2 Rising Sectors identified: {top_sectors}")
    
    # 4. Filter stocks in Top Sectors
    candidate_stocks = [
        t for t, m in stock_metrics.items() 
        if m["sector"] in top_sectors and m["momentum"] > 0 and m["sentiment"] >= 0
    ]
    logger.info(f"Found {len(candidate_stocks)} candidate stocks in rising sectors with positive momentum/news.")
    
    # 5. ML Validation
    logger.info("Validating candidates through XGBoost ML Model...")
    
    ai_validator = AIValidator()
    if ai_validator.model is None:
        logger.warning("ML model not found or disabled. Defaulting to all candidates as targets.")
        with open(TARGETS_FILE, "w") as f:
            if ACTIVE_MARKET == "US":
                json.dump([t.replace(".", "-") for t in candidate_stocks], f, indent=4)
            else:
                json.dump([f"{t.replace('.', '-')}.NS" for t in candidate_stocks], f, indent=4)
        return
        
    trend_engine = TrendEngine()
    
    approved_targets = []
    
    for symbol in candidate_stocks:
        if ACTIVE_MARKET == "US":
            yf_t = symbol.replace(".", "-")
        else:
            yf_t = f"{symbol.replace('.', '-')}.NS"
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
            features = pd.DataFrame([{
                'rsi': signal.rsi,
                'macd_signal': 1 if signal.macd_signal == "bullish" else (-1 if signal.macd_signal == "bearish" else 0),
                'ema_signal': 1 if signal.ema_signal == "bullish" else (-1 if signal.ema_signal == "bearish" else 0),
                'vwap_signal': 1 if signal.vwap_signal == "above" else -1,
                'overall_trend': signal.overall_trend,
                'sentiment_score': stock_metrics[symbol]["sentiment"]
            }])
            prob_success = ai_validator.model.predict_proba(features)[0][1]
            
            if prob_success >= 0.55:  # Slightly lower threshold for pre-market broad scanning
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
    
    logger.info(f"Selected {len(final_targets)} final targets for today's trading session.")
    for t in final_targets:
        logger.info(f"  -> {t['symbol']} ({t['sector']}) | ML: {t['ml_confidence']*100:.1f}%")
        
    with open(TARGETS_FILE, "w") as f:
        json.dump([t["symbol"] for t in final_targets], f, indent=4)
        
    logger.info(f"Saved daily targets to {TARGETS_FILE}")

if __name__ == "__main__":
    run_scanner()
