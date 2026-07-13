"""
ai_validator.py
================
Provides an ML assurance layer to validate trading decisions using a local
XGBoost model, replacing the legacy Gemini LLM dependency.
"""

import logging
import os
import joblib
import pandas as pd
from datetime import datetime

from config import config
from decision_engine import Decision
from trend_engine import TrendSignal
from db import TradingDB

logger = logging.getLogger(__name__)

class AIValidator:
    def __init__(self):
        self.enabled = config.ai.enabled
        self.validate_sells = config.ai.validate_sells
        
        self._in_docker = os.environ.get("TRADES_CSV_PATH") is not None or os.path.exists("/.dockerenv")
        self.active_market = os.getenv("TRADING_MARKET", "IN").upper()
        
        self.model_day = None
        self.model_swing = None
        self._db = TradingDB()
        if self.enabled:
            self._load_models()
            
    def _get_model_path(self, mode: str) -> str:
        model_filename = f"ml_validator_model_{self.active_market}_{mode}.pkl"
        model_path = f"/app/data/{model_filename}" if self._in_docker else f"data/{model_filename}"
        if not os.path.exists(model_path):
            local_fallback = os.path.join(os.path.dirname(__file__), "data", model_filename)
            if os.path.exists(local_fallback):
                model_path = local_fallback
        return model_path

    def _load_models(self):
        self.model_day = self._load_single_model("day")
        self.model_swing = self._load_single_model("swing")
        if self.model_day is None and self.model_swing is None:
            self.enabled = False

    def _load_single_model(self, mode: str):
        path = self._get_model_path(mode)
        try:
            if os.path.exists(path):
                model = joblib.load(path)
                logger.info(f"Successfully loaded {mode.upper()} ML Validator model from {path}")
                return model
            else:
                logger.warning(f"{mode.upper()} ML Validator model not found at {path}. Please run ml_trainer.py first.")
                return None
        except Exception as e:
            logger.error(f"Failed to load {mode.upper()} ML Validator model: {e}")
            return None

    def reload_model(self):
        """Reloads the ML models from disk (e.g., after automated retraining)."""
        logger.info("Reloading ML Validator models...")
        self.enabled = config.ai.enabled
        if self.enabled:
            self._load_models()

    def _save_log(self, symbol: str, action: str, approved: bool, reason: str):
        try:
            self._db.insert_ml_validation(
                timestamp=datetime.utcnow().isoformat() + "Z",
                symbol=symbol,
                action=action,
                approved=approved,
                reason=reason,
            )
        except Exception as e:
            logger.error("Failed to write ML validation log to DB: %s", e)

    def get_ml_confidence(self, trend_signal: TrendSignal, sentiment_score: float, mode: str = "day") -> float:
        """
        Returns the raw probability of success (0.0 to 1.0) from the specified ML model (day or swing).
        """
        model = self.model_day if mode == "day" else self.model_swing
        if not self.enabled or model is None:
            return 0.0

        try:
            macd_val = 1 if trend_signal.macd_signal == "bullish" else (-1 if trend_signal.macd_signal == "bearish" else 0)
            ema_val = 1 if trend_signal.ema_signal == "bullish" else (-1 if trend_signal.ema_signal == "bearish" else 0)
            vwap_val = 1 if trend_signal.vwap_signal == "above" else -1

            features = pd.DataFrame([{
                'rsi': trend_signal.rsi,
                'rsi_slope': trend_signal.rsi_slope,
                'macd_signal': macd_val,
                'ema_signal': ema_val,
                'vwap_signal': vwap_val,
                'sentiment_score': sentiment_score,
                'adx': trend_signal.adx,
                'atr_pct': trend_signal.atr_pct,
                'volume_ratio': trend_signal.volume_ratio,
                'bb_position': trend_signal.bb_position,
                'price_vs_sma50': trend_signal.price_vs_sma50,
            }])
            
            if hasattr(model, 'feature_names_in_'):
                expected_features = list(model.feature_names_in_)
                features = features[expected_features]
            
            prob_success = model.predict_proba(features)[0][1]
            return float(prob_success)
        except Exception as e:
            logger.error(f"Failed to calculate {mode} ML confidence: {e}")
            return 0.0

    def validate_decision(
        self,
        symbol: str,
        trend_signal_day: TrendSignal,
        trend_signal_swing: TrendSignal,
        sentiment_score: float,
        decision: Decision
    ) -> Decision:
        """
        Validates a trading decision. Uses 'day' model for BUYs, 'swing' model for SELLs.
        """
        mode = "day" if decision.action == "BUY" else "swing"
        model = self.model_day if mode == "day" else self.model_swing
        trend_signal = trend_signal_day if mode == "day" else trend_signal_swing
        
        if not self.enabled or model is None:
            return decision
            
        if decision.action == "HOLD":
            try:
                prob_day = self.get_ml_confidence(trend_signal_day, sentiment_score, mode="day")
                prob_swing = self.get_ml_confidence(trend_signal_swing, sentiment_score, mode="swing")
                decision.ml_confidence = prob_day
                decision.ml_confidence_swing = prob_swing
                decision.ai_decision = "GHOST_EVALUATED"
                decision.ai_reason = f"ML Validator evaluated HOLD (Day: {prob_day*100:.1f}%, Swing: {prob_swing*100:.1f}%)"
                return decision
            except Exception as e:
                logger.error("ML Validation failed for %s HOLD: %s", symbol, e)
                decision.ai_decision = "GHOST_EVALUATED"
                decision.ai_reason = f"Bypassed: ML Model Error ({str(e)})"
                return decision

        logger.info("Requesting %s ML validation for %s %s decision...", mode.upper(), symbol, decision.action)

        try:
            prob_success = self.get_ml_confidence(trend_signal, sentiment_score, mode=mode)
            decision.ml_confidence = prob_success
            
            if decision.action == "BUY":
                # User requested swing confidence to be available for BUY decisions as well
                try:
                    prob_success_swing = self.get_ml_confidence(trend_signal_swing, sentiment_score, mode="swing")
                    decision.ml_confidence_swing = prob_success_swing
                except Exception as e:
                    logger.error("Swing ML Validation failed for %s BUY: %s", symbol, e)
                
                approved = prob_success >= 0.60
                reason = f"ML Validator ({mode.upper()}) {'APPROVED' if approved else 'REJECTED'} BUY (Confidence: {prob_success*100:.1f}%)"
            elif decision.action == "SELL":
                decision.ml_confidence_swing = prob_success
                approved = prob_success <= 0.40
                reason = f"ML Validator ({mode.upper()}) {'APPROVED' if approved else 'REJECTED'} SELL (Confidence of upward trend: {prob_success*100:.1f}%)"

            self._save_log(symbol, decision.action, bool(approved), reason)
            
            if approved:
                logger.info(f"ML Gate PASSED: {reason}")
                decision.ai_decision = "APPROVED"
            else:
                logger.info(f"ML Gate BLOCKED: {reason}")
                decision.ai_decision = "REJECTED"
                # Actually block the trade — change action to HOLD
                decision.action = "HOLD"
                decision.reason = reason
            decision.ai_reason = reason
            return decision
                
        except Exception as e:
            logger.error("ML Validation failed for %s: %s", symbol, e)
            reason_msg = f"Bypassed: ML Model Error ({str(e)})"
            self._save_log(symbol, decision.action, True, reason_msg)
            decision.ai_decision = "GHOST_APPROVED"
            decision.ai_reason = reason_msg
            return decision
