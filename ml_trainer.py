"""
ml_trainer.py
=============
Bootstraps historical data for Nifty 50 stocks, calculates technical indicators,
and trains the initial XGBoost model to replace the Gemini LLM.
"""

import os
import numpy as np
import pandas as pd
import yfinance as yf
import xgboost as xgb
import joblib
import logging
from datetime import datetime, timedelta

from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ACTIVE_MARKET = os.getenv("TRADING_MARKET", "IN").upper()

# Load symbols from config
SYMBOLS = config.universe.tickers
MODEL_PATH = os.path.join(os.path.dirname(__file__), "data", "ml_validator_model.pkl")

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).fillna(0)
    loss = (-delta.where(delta < 0, 0)).fillna(0)
    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    return 100 - (100 / (1 + rs))

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Engineer features matching trend_engine.py"""
    df = df.copy()
    
    # RSI
    df['rsi'] = calculate_rsi(df['Close'], 14)
    
    # EMA
    df['ema_9'] = df['Close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['Close'].ewm(span=21, adjust=False).mean()
    df['ema_signal'] = np.where(df['ema_9'] > df['ema_21'], 1, -1)
    
    # MACD
    ema_12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema_12 - ema_26
    df['macd_sig_line'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_signal'] = np.where(df['macd'] > df['macd_sig_line'], 1, -1)
    
    # VWAP proxy for daily data (Typical Price SMA)
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    df['vwap_proxy'] = typical_price.rolling(window=14).mean()
    df['vwap_signal'] = np.where(df['Close'] > df['vwap_proxy'], 1, -1)
    
    # Overall Trend
    df['overall_trend'] = (df['ema_signal'] + df['macd_signal'] + df['vwap_signal']) / 3.0
    
    # Simulated Sentiment (Proxy based on 2-day momentum, mimics momentum sentiment in sentiment_engine)
    momentum = df['Close'].pct_change(periods=2) * 20
    df['sentiment_score'] = momentum.clip(-1.0, 1.0).fillna(0.0)
    
    # Target: 5-day forward return > 1%
    df['future_5d_return'] = df['Close'].shift(-5) / df['Close'] - 1
    df['target'] = np.where(df['future_5d_return'] > 0.01, 1, 0)
    
    # Drop NaNs created by shifts/rolling
    return df.dropna()

def train_model():
    logger.info("Fetching historical data and engineering features for Nifty 50...")
    
    all_data = []
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365 * 5) # 5 years
    
    for sym in SYMBOLS:
        try:
            # Resolve Yahoo symbol
            yf_sym = sym.strip().upper()
            if ACTIVE_MARKET == "US":
                yf_sym = yf_sym.replace(".", "-")
            elif not yf_sym.endswith(".NS"):
                yf_sym = yf_sym.replace(".", "-") + ".NS"
                
            ticker = yf.Ticker(yf_sym)
            df = ticker.history(start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
            if df.empty:
                logger.warning(f"No historical data found for {yf_sym}")
                continue
                
            df_features = build_features(df)
            all_data.append(df_features)
            logger.info(f"Fetched and processed {len(df_features)} rows for {yf_sym}")
        except Exception as e:
            logger.warning(f"Failed to fetch data for {sym}: {e}")
            
    if not all_data:
        logger.error("No data fetched. Aborting training.")
        return
        
    full_df = pd.concat(all_data, ignore_index=True)
    
    features = ['rsi', 'macd_signal', 'ema_signal', 'vwap_signal', 'overall_trend', 'sentiment_score']
    X = full_df[features]
    y = full_df['target']
    
    logger.info(f"Training XGBoost on {len(X)} samples...")
    
    # XGBoost setup for aggressive risk management
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
    
    # Ensure data directory exists
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    
    joblib.dump(clf, MODEL_PATH)
    logger.info(f"Model successfully saved to {MODEL_PATH}")
    
    # Log Feature Importances
    importances = clf.feature_importances_
    for feat, imp in zip(features, importances):
        logger.info(f"Feature Importance - {feat}: {imp:.4f}")

if __name__ == "__main__":
    train_model()
