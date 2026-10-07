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
from sklearn.metrics import accuracy_score, brier_score_loss, classification_report
from sklearn.isotonic import IsotonicRegression

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

def label_from_pnl(pnl) -> "int | None":
    """Real trade outcome -> training label, or None when pnl is unknown.

    exit_reason (e.g. "TRAILING_STOP") says nothing about whether a trade
    actually made money -- a trailing stop can still close at a small loss
    on a gap or slippage. Only the real pnl sign is the outcome; this must
    never special-case an exit_reason into an automatic win.
    """
    if pnl is None or (isinstance(pnl, float) and pd.isna(pnl)):
        return None
    return 1 if pnl > 0 else 0


def relative_threshold_floor(base_rate: float, relative_lift: float) -> float:
    """base_rate * (1 + relative_lift) -- see VettingConfig.threshold_relative_lift.

    Not an absolute 0.50: that assumes 50%+ calibrated confidence is
    achievable regardless of how rare the label's positive class is.
    Confirmed 2026-10-07 on the real 30-symbol US anchor universe: with a
    ~26% base rate, every per-symbol threshold collapsed to a flat 0.50
    under an absolute floor, and raising the selection percentile from 85
    to 95 didn't help either -- the live raw-score ceiling (~0.78, from
    1,719 real BUY evaluations, never once reaching 0.80) maps to
    calibrated confidence well under 50%. Gating on relative lift over the
    label's own base rate is achievable where an absolute 50% isn't.
    """
    return base_rate * (1.0 + relative_lift)


def fit_calibrator(oof_probs, oof_labels) -> IsotonicRegression:
    """Map raw predict_proba -> an honest probability, using OUT-OF-FOLD
    predictions only (never the final model's own training data, which
    would just calibrate the model to agree with itself).

    XGBoost's raw predict_proba is a reasonable RANKING but not a true
    probability -- isotonic regression fits the monotonic step function
    that makes "0.60" actually mean "60% of these actually won".
    """
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(oof_probs, oof_labels)
    return iso


def calibration_report(oof_probs, oof_labels, calibrator: IsotonicRegression) -> dict:
    """Brier score before/after calibration -- lower is better, 0.25 is the
    no-skill baseline for a 50/50 base rate. Calibration can only make the
    reported number honest; it cannot manufacture discrimination a model
    doesn't have.
    """
    calibrated = calibrator.transform(oof_probs)
    return {
        "brier_raw": float(brier_score_loss(oof_labels, oof_probs)),
        "brier_calibrated": float(brier_score_loss(oof_labels, calibrated)),
        "n": len(oof_labels),
    }


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
    # Train DAY model (5m bars, 60 days, >0.75% within 36 periods / 3 hours).
    # Cost-aware label: the old +0.2%/1h target taught the model to find
    # moves SMALLER than round-trip friction (~0.25-1% on small accounts) —
    # a perfectly accurate model would still lose money. The label must be a
    # move worth taking after costs.
    day_success = _train_single_model(
        mode="day",
        period="60d",
        interval="5m",
        future_periods=36,
        target_return=0.0075
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
                    is_win = label_from_pnl(trade['pnl'])
                    if is_win is None:
                        continue
                    sym = trade['symbol'].replace('.NS', '')
                    trade_date = str(trade['date'])

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
    oof_probs_parts, oof_labels_parts, oof_symbols_parts = [], [], []
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
        # Pool every fold's held-out (never-trained-on) predictions -- this
        # is the honest basis for calibration, same spirit as the accuracy
        # check above, just kept instead of thrown away.
        oof_probs_parts.append(clf_fold.predict_proba(X_test)[:, 1])
        oof_labels_parts.append(y_test.to_numpy())
        # full_df is symbol-major concatenated (pd.concat(..., ignore_index=True)),
        # NOT chronologically interleaved across symbols -- a single fold's test
        # slice only ever lands inside whichever few symbols' contiguous row
        # blocks that slice happens to fall in. Tag each OOF row with its
        # symbol now so per-symbol thresholds below can pool across ALL 5
        # folds instead of relying on just one fold ever covering a symbol.
        oof_symbols_parts.append(full_df.iloc[test_idx]['symbol'].to_numpy())

    avg_accuracy = np.mean(test_accuracies)
    logger.info(f"[{mode.upper()}] === Average test accuracy across 5 folds: {avg_accuracy:.3f} ===")

    if avg_accuracy < 0.52:
        logger.warning(f"[{mode.upper()}] WARNING: Model accuracy ({avg_accuracy:.3f}) is barely above random chance!")

    # Fit the calibrator on pooled out-of-fold predictions ONLY -- fitting it
    # on the final model's own training data would just calibrate the model
    # to agree with itself, hiding exactly the miscalibration this exists to
    # catch.
    oof_probs = np.concatenate(oof_probs_parts)
    oof_labels = np.concatenate(oof_labels_parts)
    calibrator = fit_calibrator(oof_probs, oof_labels)
    cal_report = calibration_report(oof_probs, oof_labels, calibrator)
    logger.info(
        f"[{mode.upper()}] Calibration (n={cal_report['n']} OOF predictions): "
        f"Brier raw={cal_report['brier_raw']:.4f} -> calibrated={cal_report['brier_calibrated']:.4f} "
        f"(lower is better; 0.25 = no-skill baseline at a 50/50 base rate)"
    )
    if cal_report["brier_calibrated"] > cal_report["brier_raw"]:
        logger.warning(
            f"[{mode.upper()}] Calibration made Brier score WORSE "
            f"({cal_report['brier_raw']:.4f} -> {cal_report['brier_calibrated']:.4f}) -- "
            "likely too little OOF data for isotonic regression to find a stable curve."
        )

    # Train final model on ALL data for deployment
    clf.fit(X, y)

    os.makedirs(os.path.dirname(model_path_local), exist_ok=True)
    # Atomic write: the trader may reload this file at any moment. Bundle the
    # calibrator with the model -- ai_validator.py applies it to every
    # predict_proba() call so "confidence %" means what it says.
    joblib.dump({"model": clf, "calibrator": calibrator}, model_path_local + ".tmp")
    os.replace(model_path_local + ".tmp", model_path_local)
    logger.info(f"[{mode.upper()}] Model successfully saved to {model_path_local}")

    # Calculate dynamic thresholds from test-set predictions only (honest thresholds),
    # in CALIBRATED probability space so "0.55" means the same thing the
    # calibration report above just measured.
    #
    # Pool OOF predictions across ALL 5 folds, not just the last one -- the
    # last-fold-only approach this used to be only ever covered whichever
    # few symbols' row blocks happened to fall in that one slice. Confirmed
    # 2026-10-07 on the real 30-symbol US anchor universe: the last fold
    # alone covered just 5 of 30 symbols; the other 25 silently fell
    # through to the "insufficient data" fallback below on EVERY single
    # training run. Pooling all 5 folds covers 25 of 30 -- the remaining 5
    # (whichever symbols land entirely in TimeSeriesSplit's initial,
    # never-tested training-only segment) genuinely have no OOF data at
    # any pool size and correctly still need that fallback.
    oof_symbols = np.concatenate(oof_symbols_parts)
    oof_calibrated = calibrator.transform(oof_probs)
    test_df = pd.DataFrame({'symbol': oof_symbols, 'pred_prob': oof_calibrated})

    # Same percentile agents/vetting.py uses (config.vetting.dynamic_threshold_pctile)
    # -- this used to be a separate hardcoded 85 here, silently able to drift
    # from vetting's value even though both compute "the same" per-symbol bar.
    pctile = config.vetting.dynamic_threshold_pctile

    # RELATIVE floor, not an absolute 0.50 -- see VettingConfig.threshold_relative_lift.
    # An absolute floor assumes 50%+ calibrated confidence is achievable; with
    # this label's own ~base_rate win frequency, it structurally often isn't.
    # Confirmed 2026-10-07: an absolute floor collapsed every one of 30 anchor
    # symbols' thresholds to a flat 0.50, and raising the percentile to 95
    # didn't fix it either -- the live raw-score ceiling never reaches the
    # calibration curve's genuine >50%-win-rate region.
    base_rate = float(y.mean())
    floor = relative_threshold_floor(base_rate, config.vetting.threshold_relative_lift)
    logger.info(
        f"[{mode.upper()}] Label base rate={base_rate:.4f} -> relative threshold "
        f"floor={floor:.4f} (vs the old flat 0.50)"
    )

    thresholds = {}
    all_thresholds = []
    for sym in full_df['symbol'].unique():
        sym_test = test_df[test_df['symbol'] == sym]
        if not sym_test.empty and len(sym_test) >= 10:
            thresh = np.percentile(sym_test['pred_prob'], pctile)
        else:
            # Fallback: use full dataset if insufficient test data for this symbol
            sym_full = full_df[full_df['symbol'] == sym]
            full_probs = calibrator.transform(clf.predict_proba(X.loc[sym_full.index])[:, 1])
            thresh = np.percentile(full_probs, pctile)
        thresh = float(np.clip(thresh, floor, 0.95))
        clean_sym = sym.replace('.NS', '') if ACTIVE_MARKET == "IN" else sym
        thresholds[clean_sym] = thresh
        all_thresholds.append(thresh)

    # Global threshold: used as fallback for any symbol NOT in training set
    # (e.g., sector scanner picks MRNA, AFRM, etc. which aren't training symbols)
    global_thresh = float(np.percentile(all_thresholds, 75))  # 75th pct of per-symbol thresholds
    # Persisted so agents/vetting.py's OWN percentile clip (computed from
    # backtest replay, which has no labeled ground truth to derive a base
    # rate from directly) applies the exact same floor instead of drifting
    # back to an independent guess.
    thresholds["_FLOOR_"] = float(floor)
    thresholds["_GLOBAL_"] = global_thresh
    logger.info(f"[{mode.upper()}] Global fallback threshold: {global_thresh:.4f}")
    logger.info(f"[{mode.upper()}] Per-symbol threshold range: {min(all_thresholds):.4f} - {max(all_thresholds):.4f}")
            
    thresholds_path = os.path.join(os.path.dirname(__file__), "data", f"ml_thresholds_{ACTIVE_MARKET}_{mode}.json")
    # Atomic write: decision_engine hot-reloads this file on mtime change
    with open(thresholds_path + ".tmp", 'w') as f:
        json.dump(thresholds, f, indent=4)
    os.replace(thresholds_path + ".tmp", thresholds_path)
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
