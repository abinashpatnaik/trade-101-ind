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
        self.model_path = "/app/data/ml_validator_model.pkl" if self._in_docker else "data/ml_validator_model.pkl"
        
        # We also look in the current directory if it's not found in data/ (e.g. testing)
        if not os.path.exists(self.model_path):
            local_fallback = os.path.join(os.path.dirname(__file__), "data", "ml_validator_model.pkl")
            if os.path.exists(local_fallback):
                self.model_path = local_fallback

        self.model = None
        self._db = TradingDB()
        if self.enabled:
            self._load_model()
            
    def _load_model(self):
        try:
            if os.path.exists(self.model_path):
                self.model = joblib.load(self.model_path)
                logger.info("Successfully loaded local ML Validator model from %s", self.model_path)
            else:
                logger.warning("ML Validator model not found at %s. Please run ml_trainer.py first. Validation will be bypassed.", self.model_path)
                self.enabled = False
        except Exception as e:
            logger.error("Failed to load ML Validator model: %s", e)
            self.enabled = False

    def reload_model(self):
        """Reloads the ML model from disk (e.g., after automated retraining)."""
        logger.info("Reloading ML Validator model from %s...", self.model_path)
        self.enabled = config.ai.enabled
        if self.enabled:
            self._load_model()

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

    def validate_decision(
        self,
        symbol: str,
        trend_signal: TrendSignal,
        sentiment_score: float,
        decision: Decision
    ) -> Decision:
        """
        Validates a trading decision using the local XGBoost model.
        Returns the original decision if approved, or a modified HOLD decision if rejected.
        """
        if not self.enabled or self.model is None:
            return decision

        logger.info("Requesting ML validation for %s %s decision...", symbol, decision.action)

        try:
            # Convert string signals to numeric values matching training data
            macd_val = 1 if trend_signal.macd_signal == "bullish" else (-1 if trend_signal.macd_signal == "bearish" else 0)
            ema_val = 1 if trend_signal.ema_signal == "bullish" else (-1 if trend_signal.ema_signal == "bearish" else 0)
            vwap_val = 1 if trend_signal.vwap_signal == "above" else -1

            # Construct feature vector exactly as trained
            features = pd.DataFrame([{
                'rsi': trend_signal.rsi,
                'macd_signal': macd_val,
                'ema_signal': ema_val,
                'vwap_signal': vwap_val,
                'overall_trend': trend_signal.overall_trend,
                'sentiment_score': sentiment_score,
                'adx': trend_signal.adx,
                'volume_ratio': trend_signal.volume_ratio
            }])
            
            # Ensure backward compatibility with older models trained on fewer features
            if hasattr(self.model, 'feature_names_in_'):
                expected_features = list(self.model.feature_names_in_)
                features = features[expected_features]
            
            # Predict Probability of Success (Class 1)
            # XGBoost predict_proba returns array of [prob_0, prob_1]
            prob_success = self.model.predict_proba(features)[0][1]
            decision.ml_confidence = float(prob_success)
            
            # Risk Management Thresholds
            # For BUY: we want high confidence of success (e.g., > 60%)
            # For SELL: a successful BUY is unlikely, so prob_success should be low (e.g., < 40%)
            if decision.action == "BUY":
                approved = prob_success >= 0.60
                reason = f"ML Validator {'APPROVED' if approved else 'REJECTED'} BUY (Confidence: {prob_success*100:.1f}%)"
            elif decision.action == "SELL":
                approved = prob_success <= 0.40
                reason = f"ML Validator {'APPROVED' if approved else 'REJECTED'} SELL (Confidence of upward trend: {prob_success*100:.1f}%)"
            else:
                approved = True
                reason = f"ML Validator EVALUATED {decision.action} (Confidence: {prob_success*100:.1f}%)"

            self._save_log(symbol, decision.action, bool(approved), reason)
            
            # GHOST MODE: Always let the trade pass, but log the ai_decision
            if decision.action in ("BUY", "SELL"):
                logger.info(f"Ghost Mode: {reason}")
            decision.ai_decision = "GHOST_APPROVED" if approved else "GHOST_REJECTED"
            if decision.action == "HOLD":
                decision.ai_decision = "GHOST_EVALUATED"
            decision.ai_reason = reason
            return decision
                
        except Exception as e:
            logger.error("ML Validation failed for %s: %s", symbol, e)
            reason_msg = f"Bypassed: ML Model Error ({str(e)})"
            self._save_log(symbol, decision.action, True, reason_msg)
            decision.ai_decision = "GHOST_APPROVED"
            decision.ai_reason = reason_msg
            return decision
