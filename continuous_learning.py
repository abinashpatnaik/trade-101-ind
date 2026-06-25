"""
continuous_learning.py
======================
Handles the online learning aspect of the ML Validator.
Logs daily features (including fresh news sentiment) and periodically
retrains the XGBoost model so it learns from new market regimes.
"""

import os
import logging
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import joblib
import xgboost as xgb

from config import config

logger = logging.getLogger(__name__)

ACTIVE_MARKET = os.getenv("TRADING_MARKET", "IN").upper()

class ContinuousLearning:
    def __init__(self):
        self._in_docker = os.environ.get("TRADES_CSV_PATH") is not None or os.path.exists("/.dockerenv")
        self.features_log_path = "/app/data/training_features.csv" if self._in_docker else "data/training_features.csv"
        self.model_path = "/app/data/ml_validator_model.pkl" if self._in_docker else "data/ml_validator_model.pkl"
        
        # We also look in the current directory if it's not found in data/ (e.g. testing)
        local_fallback = os.path.join(os.path.dirname(__file__), "data", "training_features.csv")
        if not self._in_docker and not os.path.exists(self.features_log_path) and os.path.exists(local_fallback):
            self.features_log_path = local_fallback
            
        os.makedirs(os.path.dirname(self.features_log_path), exist_ok=True)

    def log_daily_features(self, symbol: str, trend_signal, sentiment_score: float, predicted_prob: float = 0.0):
        """
        Appends the day's features to the CSV. The 'target' is left empty 
        and filled in later by the retrain script after 5 days have passed.
        """
        new_row = {
            "date": datetime.utcnow().strftime('%Y-%m-%d'),
            "symbol": symbol,
            "rsi": trend_signal.rsi,
            "macd_signal": trend_signal.macd_signal,
            "ema_signal": trend_signal.ema_signal,
            "vwap_signal": trend_signal.vwap_signal,
            "overall_trend": trend_signal.overall_trend,
            "sentiment_score": sentiment_score,
            "adx": trend_signal.adx,
            "volume_ratio": trend_signal.volume_ratio,
            "predicted_prob": predicted_prob,
            "target": None  # Unknown until 5 days pass
        }
        
        df_new = pd.DataFrame([new_row])
        
        if os.path.exists(self.features_log_path):
            df = pd.read_csv(self.features_log_path)
            # Avoid duplicate logs for the same day/symbol
            if not ((df['date'] == new_row['date']) & (df['symbol'] == new_row['symbol'])).any():
                df = pd.concat([df, df_new], ignore_index=True)
                df.to_csv(self.features_log_path, index=False)
        else:
            df_new.to_csv(self.features_log_path, index=False)

    def retrain_model_if_needed(self):
        """
        Fills in missing targets using recent price data and retrains the model 
        if enough new data has accumulated.
        """
        if not os.path.exists(self.features_log_path):
            return

        logger.info("Checking for continuous learning updates...")
        df = pd.read_csv(self.features_log_path)
        
        unlabeled = df[df['target'].isna()]
        if unlabeled.empty:
            logger.info("No unlabeled rows to process.")
            return

        symbols = unlabeled['symbol'].unique()
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        
        updated_count = 0
        for sym in symbols:
            try:
                # Fetch recent prices to see if 5 days have passed for unlabeled rows
                yf_sym = sym.strip().upper()
                if ACTIVE_MARKET == "US":
                    yf_sym = yf_sym.replace(".", "-")
                elif not yf_sym.endswith(".NS"):
                    yf_sym = yf_sym.replace(".", "-") + ".NS"
                    
                ticker = yf.Ticker(yf_sym)
                hist = ticker.history(start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
                if hist.empty:
                    continue
                
                # Align dates
                hist.index = hist.index.strftime('%Y-%m-%d')
                
                mask = (df['symbol'] == sym) & (df['target'].isna())
                for idx, row in df[mask].iterrows():
                    date_str = row['date']
                    if date_str in hist.index:
                        loc = hist.index.get_loc(date_str)
                        # Ensure we have 5 days into the future
                        if loc + 5 < len(hist):
                            current_close = hist.iloc[loc]['Close']
                            future_close = hist.iloc[loc + 5]['Close']
                            future_return = (future_close / current_close) - 1
                            df.at[idx, 'target'] = 1 if future_return > 0.01 else 0
                            updated_count += 1
            except Exception as e:
                logger.warning(f"Failed to fetch update data for {sym}: {e}")

        if updated_count > 0:
            df.to_csv(self.features_log_path, index=False)
            logger.info(f"Updated {updated_count} rows with actual market outcomes.")
            
            # Retrain model
            labeled_df = df.dropna(subset=['target'])
            if len(labeled_df) > 50:  # Arbitrary minimum threshold to allow retraining
                features = ['rsi', 'macd_signal', 'ema_signal', 'vwap_signal', 'overall_trend', 'sentiment_score', 'adx', 'volume_ratio']
                X = labeled_df[features]
                y = labeled_df['target'].astype(int)
                
                logger.info("Retraining XGBoost model on aggregated dataset...")
                clf = xgb.XGBClassifier(
                    n_estimators=100,
                    max_depth=4,
                    learning_rate=0.05,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    eval_metric='logloss',
                    random_state=42
                )
                clf.fit(X, y)
                joblib.dump(clf, self.model_path)
                logger.info(f"Model successfully retrained and saved to {self.model_path}")
