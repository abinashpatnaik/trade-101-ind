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
import json
from datetime import datetime, timedelta
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, classification_report

from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ACTIVE_MARKET = os.getenv("TRADING_MARKET", "IN").upper()

# Load anchor symbols from config
ANCHOR_SYMBOLS = config.universe.tickers
SYMBOLS = list(ANCHOR_SYMBOLS)

# Load daily targets from sector scanner if available
TARGETS_FILE = os.path.join(os.path.dirname(__file__), "data", f"daily_targets_{ACTIVE_MARKET}.json")
if os.path.exists(TARGETS_FILE):
    try:
        with open(TARGETS_FILE, "r") as f:
            daily_targets = json.load(f)
            # Add new targets, remove ".NS" if present to match config style
            for t in daily_targets:
                clean_t = t.replace(".NS", "")
                if clean_t not in SYMBOLS:
                    SYMBOLS.append(clean_t)
        logger.info(f"Loaded {len(daily_targets)} daily targets. Total training universe: {len(SYMBOLS)}")
    except Exception as e:
        logger.error(f"Failed to load daily targets: {e}")
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
    """Engineer features matching trend_engine.py — v2 with improved feature set."""
    df = df.copy()
    
    # RSI
    df['rsi'] = calculate_rsi(df['Close'], 14)
    
    # RSI Slope (rate of change of RSI over 3 periods — detects momentum shifts)
    df['rsi_slope'] = df['rsi'].diff(3).fillna(0.0)
    
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
    
    # REMOVED: overall_trend was a redundant linear combination of ema/macd/vwap signals
    # Instead, the model can learn the combination itself with more discriminative power
    
    # Sentiment: set to 0.0 (neutral) during training to avoid train/serve skew.
    # At inference time, real sentiment is provided. By training with neutral sentiment,
    # the model learns to NOT rely on this noisy feature, preventing corruption.
    df['sentiment_score'] = 0.0
    
    # ADX (Average Directional Index — trend strength)
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
    
    # ATR Percentage (volatility relative to price — helps model avoid high-noise stocks)
    df['atr_pct'] = (atr_smooth / df['Close']).fillna(0.0) * 100
    
    # Volume Ratio
    avg_vol = df['Volume'].rolling(window=20, min_periods=1).mean()
    df['volume_ratio'] = (df['Volume'] / avg_vol.replace(0, 1)).fillna(1.0)
    
    # Bollinger Band Position (where price sits within the bands, 0-1)
    sma_20 = df['Close'].rolling(window=20).mean()
    std_20 = df['Close'].rolling(window=20).std()
    bb_upper = sma_20 + (2 * std_20)
    bb_lower = sma_20 - (2 * std_20)
    bb_width = (bb_upper - bb_lower).replace(0, 1e-9)
    df['bb_position'] = ((df['Close'] - bb_lower) / bb_width).clip(0, 1).fillna(0.5)
    
    # Price vs SMA50 (longer-term trend context)
    sma_50 = df['Close'].rolling(window=50).mean()
    df['price_vs_sma50'] = ((df['Close'] / sma_50) - 1.0).fillna(0.0) * 100  # % above/below
    
    return df
    
def train_model():
    logger.info("Starting Dual-Model Training Process...")
    swing_success = _train_single_model(
        mode="swing",
        period="5y",
        interval="1d",
        future_periods=5,
        target_return=0.01
    )
    # Train DAY model (5m bars, 60 days, >0.2% in 12 periods)
    day_success = _train_single_model(
        mode="day",
        period="60d",
        interval="5m",
        future_periods=12,
        target_return=0.002
    )
    return swing_success and day_success

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
            
            df_features['symbol'] = sym.strip().upper()
            all_data.append(df_features)
            logger.info(f"[{mode.upper()}] Fetched and processed {len(df_features)} rows for {yf_sym}")
            
            # Rate limiting to prevent yfinance bans
            import time
            time.sleep(1.0)
        except Exception as e:
            logger.warning(f"[{mode.upper()}] Failed to fetch data for {sym}: {e}")
            
    if not all_data:
        logger.error(f"[{mode.upper()}] No data fetched. Aborting training.")
        return False
        
    full_df = pd.concat(all_data, ignore_index=True)
    
    # --- CONTINUOUS LEARNING: Inject Real Trade Outcomes ---
    db_path = os.path.join(os.path.dirname(__file__), "data", f"trading_{ACTIVE_MARKET}.db")
    if os.path.exists(db_path):
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            # Get historical BUY trades
            trades_df = pd.read_sql_query("SELECT symbol, pnl, exit_reason, date, time FROM trades WHERE action='BUY' AND exit_reason IS NOT NULL", conn)
            conn.close()
            
            if not trades_df.empty:
                logger.info(f"[{mode.upper()}] Loaded {len(trades_df)} historical trades for continuous learning.")
                
                # Add date_str to index for alignment
                full_df['date_str'] = full_df.index.astype(str).str[:10]
                
                overrides = 0
                for _, trade in trades_df.iterrows():
                    sym = trade['symbol'].replace('.NS', '')
                    trade_date = str(trade['date'])
                    is_win = 1 if (trade['pnl'] > 0 or trade['exit_reason'] == "TRAILING_STOP") else 0
                    
                    mask = (full_df['symbol'] == sym) & (full_df['date_str'] == trade_date)
                    if mask.any():
                        full_df.loc[mask, 'target'] = is_win
                        overrides += mask.sum()
                
                logger.info(f"[{mode.upper()}] Applied {overrides} continuous learning target overrides based on real trades.")
                full_df = full_df.drop(columns=['date_str'])
        except Exception as e:
            logger.error(f"[{mode.upper()}] Failed to apply continuous learning from trades DB: {e}")
            
    # V2 feature set: removed redundant overall_trend, added atr_pct, bb_position, rsi_slope, price_vs_sma50
    features = ['rsi', 'rsi_slope', 'macd_signal', 'ema_signal', 'vwap_signal', 'sentiment_score', 'adx', 'atr_pct', 'volume_ratio', 'bb_position', 'price_vs_sma50']
    
    # Drop rows with NaN targets or features
    full_df = full_df.dropna(subset=['target'] + features)
    
    X = full_df[features]
    y = full_df['target']
    
    logger.info(f"[{mode.upper()}] Training XGBoost on {len(X)} samples with {len(features)} features...")
    
    scale_pos_weight = len(y[y == 0]) / max(len(y[y == 1]), 1)
    logger.info(f"[{mode.upper()}] Class balance: {len(y[y==1])} positive / {len(y[y==0])} negative (scale_pos_weight={scale_pos_weight:.2f})")
    
    clf = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric='logloss',
        scale_pos_weight=scale_pos_weight,
        random_state=42
    )
    
    # Proper train/test split using TimeSeriesSplit for honest out-of-sample evaluation
    tscv = TimeSeriesSplit(n_splits=5)
    test_accuracies = []
    for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
        clf_fold = xgb.XGBClassifier(
            n_estimators=200, max_depth=5, learning_rate=0.03,
            subsample=0.8, colsample_bytree=0.8, eval_metric='logloss',
            scale_pos_weight=scale_pos_weight, random_state=42
        )
        clf_fold.fit(X_train, y_train)
        y_pred = clf_fold.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        test_accuracies.append(acc)
        logger.info(f"[{mode.upper()}] Fold {fold+1} test accuracy: {acc:.3f}")
    
    avg_accuracy = np.mean(test_accuracies)
    logger.info(f"[{mode.upper()}] === Average test accuracy across 5 folds: {avg_accuracy:.3f} ===")
    
    if avg_accuracy < 0.52:
        logger.warning(f"[{mode.upper()}] WARNING: Model accuracy ({avg_accuracy:.3f}) is barely above random chance!")
    
    # Train final model on ALL data for deployment
    clf.fit(X, y)
    
    os.makedirs(os.path.dirname(model_path_local), exist_ok=True)
    joblib.dump(clf, model_path_local)
    logger.info(f"[{mode.upper()}] Model successfully saved to {model_path_local}")
    
    # Calculate dynamic thresholds from test-set predictions only (honest thresholds)
    # Use the last fold's test predictions for threshold calibration
    last_train_idx, last_test_idx = list(tscv.split(X))[-1]
    test_probs = clf.predict_proba(X.iloc[last_test_idx])[:, 1]
    test_df = full_df.iloc[last_test_idx].copy()
    test_df['pred_prob'] = test_probs
    
    thresholds = {}
    all_thresholds = []
    for sym in full_df['symbol'].unique():
        sym_test = test_df[test_df['symbol'] == sym]
        if not sym_test.empty and len(sym_test) >= 10:
            thresh = np.percentile(sym_test['pred_prob'], 85)
        else:
            # Fallback: use full dataset if insufficient test data for this symbol
            sym_full = full_df[full_df['symbol'] == sym]
            full_probs = clf.predict_proba(X.loc[sym_full.index])[:, 1]
            thresh = np.percentile(full_probs, 85)
        # Bound the threshold between 0.50 and 0.95
        thresh = float(np.clip(thresh, 0.50, 0.95))
        clean_sym = sym.replace('.NS', '') if ACTIVE_MARKET == "IN" else sym
        thresholds[clean_sym] = thresh
        all_thresholds.append(thresh)
    
    # Global threshold: used as fallback for any symbol NOT in training set
    # (e.g., sector scanner picks MRNA, AFRM, etc. which aren't training symbols)
    global_thresh = float(np.percentile(all_thresholds, 75))  # 75th pct of per-symbol thresholds
    thresholds["_GLOBAL_"] = global_thresh
    logger.info(f"[{mode.upper()}] Global fallback threshold: {global_thresh:.4f}")
    logger.info(f"[{mode.upper()}] Per-symbol threshold range: {min(all_thresholds):.4f} - {max(all_thresholds):.4f}")
            
    thresholds_path = os.path.join(os.path.dirname(__file__), "data", f"ml_thresholds_{ACTIVE_MARKET}_{mode}.json")
    with open(thresholds_path, 'w') as f:
        json.dump(thresholds, f, indent=4)
    logger.info(f"[{mode.upper()}] Saved dynamic thresholds to {thresholds_path}")
    
    # Log feature importances to understand what the model actually learned
    importances = dict(zip(features, clf.feature_importances_))
    sorted_imp = sorted(importances.items(), key=lambda x: x[1], reverse=True)
    logger.info(f"[{mode.upper()}] Feature importances:")
    for feat, imp in sorted_imp:
        logger.info(f"  {feat}: {imp:.4f}")
    
    return True

if __name__ == "__main__":
    train_model()
