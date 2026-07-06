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
MODEL_PATH = os.path.join(os.path.dirname(__file__), "data", f"ml_validator_model_{ACTIVE_MARKET}.pkl")

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
    
    # ADX
    high = df['High']
    low = df['Low']
    close = df['Close']
    period = 14
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_smooth = tr.rolling(window=period).mean()
    plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr_smooth)
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr_smooth)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1))
    df['adx'] = dx.rolling(window=period).mean().fillna(0.0)
    
    # Volume Ratio
    avg_vol = df['Volume'].rolling(window=20, min_periods=1).mean()
    df['volume_ratio'] = (df['Volume'] / avg_vol.replace(0, 1)).fillna(1.0)
    
    return df
    
def train_model():
    logger.info("Starting Dual-Model Training Process...")
    # Train SWING model (Daily bars, 5 years, >1% in 5 days)
    _train_single_model(
        mode="swing",
        period="5y",
        interval="1d",
        future_periods=5,
        target_return=0.01
    )
    # Train DAY model (5m bars, 60 days, >0.2% in 12 periods)
    _train_single_model(
        mode="day",
        period="60d",
        interval="5m",
        future_periods=12,
        target_return=0.002
    )

def _train_single_model(mode: str, period: str, interval: str, future_periods: int, target_return: float):
    logger.info(f"[{mode.upper()}] Fetching historical data ({period}, {interval})...")
    model_path_local = os.path.join(os.path.dirname(__file__), "data", f"ml_validator_model_{ACTIVE_MARKET}_{mode}.pkl")
    
    all_data = []
    
    for sym in SYMBOLS:
        try:
            # Resolve Yahoo symbol
            yf_sym = sym.strip().upper()
            if ACTIVE_MARKET == "US":
                yf_sym = yf_sym.replace(".", "-")
            elif not yf_sym.endswith(".NS"):
                yf_sym = yf_sym.replace(".", "-") + ".NS"
            
            end_date = datetime.now()
            if mode == "swing":
                start_date = end_date - timedelta(days=365 * 5)
                df = yf.download(yf_sym, start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'), interval=interval, progress=False)
            else:
                start_date = end_date - timedelta(days=59)
                df = yf.download(yf_sym, start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'), interval=interval, progress=False)
                
            if df is None or df.empty:
                logger.warning(f"[{mode.upper()}] No historical data found for {yf_sym}")
                continue
                
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            df_features = build_features(df)
            
            # Target generation
            df_features['future_return'] = df_features['Close'].shift(-future_periods) / df_features['Close'] - 1
            df_features['target'] = np.where(df_features['future_return'] > target_return, 1, 0)
            df_features = df_features.dropna()
            
            all_data.append(df_features)
            logger.info(f"[{mode.upper()}] Fetched and processed {len(df_features)} rows for {yf_sym}")
        except Exception as e:
            logger.warning(f"[{mode.upper()}] Failed to fetch data for {sym}: {e}")
            
    if not all_data:
        logger.error(f"[{mode.upper()}] No data fetched. Aborting training.")
        return False
        
    full_df = pd.concat(all_data, ignore_index=True)
    
    features = ['rsi', 'macd_signal', 'ema_signal', 'vwap_signal', 'overall_trend', 'sentiment_score', 'adx', 'volume_ratio']
    X = full_df[features]
    y = full_df['target']
    
    logger.info(f"[{mode.upper()}] Training XGBoost on {len(X)} samples...")
    
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
    
    os.makedirs(os.path.dirname(model_path_local), exist_ok=True)
    joblib.dump(clf, model_path_local)
    logger.info(f"[{mode.upper()}] Model successfully saved to {model_path_local}")
    
    return True

if __name__ == "__main__":
    train_model()
