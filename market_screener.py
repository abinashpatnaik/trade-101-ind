#!/usr/bin/env python3
"""
market_screener.py
==================
Downloads the full master instrument list from Zerodha, filters for equities
(NSE and BSE), and performs a fast bulk download via yfinance to identify
the most liquid and high-momentum stocks. This dynamic list is then fed
to the ML models.
"""

import os
import re
import csv
import logging
import requests
import pandas as pd
import yfinance as yf
from io import StringIO

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

INSTRUMENTS_URL = "https://api.kite.trade/instruments"

def is_valid_ticker(symbol: str) -> bool:
    """Filters out indices, bonds, and ETFs that aren't standard equities."""
    if not isinstance(symbol, str):
        return False
    # Exclude symbols with spaces, $ signs, or common bond patterns
    if " " in symbol or "$" in symbol or symbol.startswith("NIFTY"):
        return False
    if re.search(r'\d+GS\d+', symbol) or re.search(r'\d+SG', symbol):
        return False
    # Avoid symbols ending in -BE, -ST (these are special series on NSE)
    if symbol.endswith("-BE") or symbol.endswith("-ST") or symbol.endswith("-SG"):
        return False
    return True

def get_dynamic_universe(top_n: int = 50) -> list[str]:
    """
    1. Downloads all NSE/BSE EQ instruments.
    2. Downloads 30-day OHLCV data.
    3. Filters by volume and price.
    4. Ranks by simple momentum/RSI.
    Returns the top N ticker symbols.
    """
    logger.info("Fetching master instrument list from Zerodha...")
    try:
        resp = requests.get(INSTRUMENTS_URL, timeout=15)
        resp.raise_for_status()
        csv_data = StringIO(resp.text)
        df = pd.read_csv(csv_data)
    except Exception as e:
        logger.error(f"Failed to fetch instruments: {e}")
        # Fallback to hardcoded list if the API fails
        from config import config
        return config.universe.tickers

    # Filter for NSE and BSE equities
    eq_df = df[(df['instrument_type'] == 'EQ') & (df['exchange'].isin(['NSE', 'BSE']))].copy()
    eq_df = eq_df[eq_df['tradingsymbol'].apply(is_valid_ticker)]
    
    # Prioritize NSE stocks. If a stock is in both, drop the BSE one.
    eq_df['is_nse'] = eq_df['exchange'] == 'NSE'
    eq_df = eq_df.sort_values('is_nse', ascending=False).drop_duplicates(subset=['tradingsymbol'])

    # Format for yfinance
    def format_yf(row):
        suffix = ".NS" if row['exchange'] == "NSE" else ".BO"
        return f"{row['tradingsymbol']}{suffix}"

    yf_tickers = eq_df.apply(format_yf, axis=1).tolist()
    
    # To avoid rate limits, we'll just take a random sample of 2000 of the most common looking ones, 
    # but since we don't have market cap, we will rely on yfinance bulk download (it can handle 2000).
    # Let's limit to 2000 to be safe on memory and API bans.
    yf_tickers = yf_tickers[:2000]

    logger.info(f"Filtered to {len(yf_tickers)} valid equities. Bulk downloading 1-month data...")
    
    # yfinance bulk download
    data = yf.download(
        " ".join(yf_tickers),
        period="1mo",
        interval="1d",
        group_by="ticker",
        threads=True,
        progress=False
    )
    
    results = []
    
    for ticker in yf_tickers:
        try:
            if ticker in data and not data[ticker].empty:
                df_ticker = data[ticker].dropna(subset=['Close'])
                if len(df_ticker) < 15:
                    continue
                
                recent = df_ticker.iloc[-5:]
                avg_vol = recent['Volume'].mean()
                last_price = recent['Close'].iloc[-1]
                
                # Liquidity Filter: Price > 50, Volume > 1M
                if last_price < 50 or avg_vol < 1000000:
                    continue
                
                # Calculate simple momentum: (Current Price / Price 20 days ago) - 1
                price_20_days_ago = df_ticker['Close'].iloc[-20] if len(df_ticker) >= 20 else df_ticker['Close'].iloc[0]
                momentum = (last_price / price_20_days_ago) - 1
                
                results.append({
                    "symbol": ticker,
                    "momentum": momentum,
                    "avg_vol": avg_vol
                })
        except Exception:
            pass

    if not results:
        logger.warning("No stocks passed the liquidity filter. Falling back to config universe.")
        from config import config
        return config.universe.tickers

    # Sort by momentum
    results_df = pd.DataFrame(results)
    top_stocks = results_df.sort_values(by="momentum", ascending=False).head(top_n)
    
    final_tickers = top_stocks["symbol"].tolist()
    logger.info(f"Selected Top {len(final_tickers)} high-momentum, liquid stocks for ML evaluation.")
    return final_tickers

def get_us_dynamic_universe(top_n: int = 50) -> list[str]:
    """
    1. Scrapes Russell 1000 tickers from Wikipedia.
    2. Downloads 30-day OHLCV data in batches to avoid rate limits.
    3. Filters by volume and price.
    4. Ranks by simple momentum/RSI.
    Returns the top N ticker symbols for the US market.
    """
    import time
    from io import StringIO
    logger.info("Fetching Russell 1000 instrument list from Wikipedia...")
    try:
        url = 'https://en.wikipedia.org/wiki/Russell_1000_Index'
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers)
        tables = pd.read_html(StringIO(r.text))
        
        # Find the table containing 'Symbol' or 'Ticker'
        russell_df = None
        for t in tables:
            if 'Symbol' in t.columns or 'Ticker' in t.columns:
                russell_df = t
                break
                
        if russell_df is None:
            raise ValueError("Could not find Ticker/Symbol column in Wikipedia tables.")
            
        col = 'Symbol' if 'Symbol' in russell_df.columns else 'Ticker'
        tickers = russell_df[col].tolist()
        
        # Clean tickers replacing '.' with '-' for yfinance (e.g. BRK.B -> BRK-B)
        yf_tickers = [str(t).replace('.', '-') for t in tickers]
    except Exception as e:
        logger.error(f"Failed to fetch Russell 1000 instruments: {e}")
        # Fallback to hardcoded list if the scrape fails
        from config import config
        return config.universe.tickers

    logger.info(f"Filtered to {len(yf_tickers)} US equities. Bulk downloading 1-month data in batches...")
    
    results = []
    batch_size = 200
    
    for i in range(0, len(yf_tickers), batch_size):
        batch_tickers = yf_tickers[i:i+batch_size]
        logger.info(f"Downloading batch {i//batch_size + 1}/{(len(yf_tickers)+batch_size-1)//batch_size}...")
        
        try:
            data = yf.download(
                " ".join(batch_tickers),
                period="1mo",
                interval="1d",
                group_by="ticker",
                threads=True,
                progress=False
            )
            
            for ticker in batch_tickers:
                try:
                    if ticker in data and not data[ticker].empty:
                        df_ticker = data[ticker].dropna(subset=['Close'])
                        if len(df_ticker) < 15:
                            continue
                        
                        recent = df_ticker.iloc[-5:]
                        avg_vol = recent['Volume'].mean()
                        last_price = recent['Close'].iloc[-1]
                        
                        # Liquidity Filter: Price > 10, Volume > 1M
                        if last_price < 10 or avg_vol < 1000000:
                            continue
                        
                        # Calculate simple momentum: (Current Price / Price 20 days ago) - 1
                        price_20_days_ago = df_ticker['Close'].iloc[-20] if len(df_ticker) >= 20 else df_ticker['Close'].iloc[0]
                        momentum = (last_price / price_20_days_ago) - 1
                        
                        results.append({
                            "symbol": ticker,
                            "momentum": momentum,
                            "avg_vol": avg_vol
                        })
                except Exception:
                    pass
            
            # Sleep to respect rate limits
            time.sleep(2)
        except Exception as e:
            logger.error(f"Error downloading batch: {e}")

    if not results:
        logger.warning("No stocks passed the US liquidity filter. Falling back to config universe.")
        from config import config
        return config.universe.tickers

    # Sort by momentum
    results_df = pd.DataFrame(results)
    top_stocks = results_df.sort_values(by="momentum", ascending=False).head(top_n)
    
    final_tickers = top_stocks["symbol"].tolist()
    logger.info(f"Selected Top {len(final_tickers)} high-momentum, liquid US stocks for ML evaluation.")
    return final_tickers

if __name__ == "__main__":
    targets = get_dynamic_universe(10)
    print("Found targets:", targets)
