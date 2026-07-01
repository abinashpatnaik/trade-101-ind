import os
import json
import time
import logging
import threading
import yfinance as yf
import pandas as pd
from config import config

logger = logging.getLogger(__name__)

ACTIVE_MARKET = os.getenv("TRADING_MARKET", "IN").upper()

class TickerFetcher:
    """
    Background daemon thread that periodically fetches daily price data
    for top 20 Nifty 50 stocks and writes the results to data/ticker.json
    so the Node.js dashboard can display live prices without hitting rate
    limits or dealing with Yahoo Finance crumb issues natively in Javascript.
    """
    def __init__(self):
        self._running = False
        self._thread = None
        
        self.symbols = []
        for s in config.universe.tickers:
            base = s.strip().upper()
            if ACTIVE_MARKET == "US":
                self.symbols.append(base.replace(".", "-"))
            elif not base.endswith(".NS"):
                self.symbols.append(base + ".NS")
            else:
                self.symbols.append(base)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        logger.info("Starting background ticker fetcher for %d symbols...", len(self.symbols))
        
        # Ensure data directory exists
        data_dir = os.path.dirname(config.agent.trades_csv)
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
        
        while self._running:
            try:
                # yfinance download is efficient for multiple symbols
                df = yf.download(self.symbols, period="5d", interval="1d", progress=False)
                
                # Check if multi-index or single level
                if df is not None and not df.empty and "Close" in df.columns.get_level_values(0):
                    closes = df["Close"]
                    
                    ticker_data = []
                    for ysym in self.symbols:
                        # Convert back to bare symbol for the dashboard
                        if ACTIVE_MARKET == "US":
                            sym_bare = ysym.replace("-", ".")
                        else:
                            sym_bare = ysym.replace(".NS", "").replace("-", ".")
                        
                        try:
                            # if multiple symbols, closes is a DataFrame with symbols as columns
                            if isinstance(closes, pd.DataFrame):
                                if ysym in closes.columns:
                                    s_data = closes[ysym].dropna()
                                else:
                                    continue
                            # if single symbol, closes is a Series
                            elif isinstance(closes, pd.Series):
                                s_data = closes.dropna()
                            else:
                                continue
                                
                            if len(s_data) >= 2:
                                cur = float(s_data.iloc[-1])
                                prev = float(s_data.iloc[-2])
                                change = ((cur - prev) / prev) * 100
                            elif len(s_data) == 1:
                                cur = float(s_data.iloc[-1])
                                change = 0.0
                            else:
                                continue
                                
                            ticker_data.append({
                                "symbol": sym_bare,
                                "price": round(cur, 2),
                                "change": round(change, 2)
                            })
                        except Exception as e:
                            logger.debug("Ticker fetch err for %s: %s", ysym, e)
                    
                    if ticker_data:
                        data_dir = os.path.dirname(config.agent.trades_csv)
                        market = os.environ.get("TRADING_MARKET", "IN")
                        ticker_filename = f"ticker_{market}.json"
                        ticker_file = os.path.join(data_dir, ticker_filename) if data_dir else ticker_filename
                        with open(ticker_file, "w") as f:
                            json.dump({"ticker": ticker_data}, f)
            except Exception as e:
                logger.error("Ticker fetcher failed: %s", e)
            
            # Sleep 60 seconds (in chunks to allow graceful exit)
            slept = 0
            while slept < 60 and self._running:
                time.sleep(5)
                slept += 5
