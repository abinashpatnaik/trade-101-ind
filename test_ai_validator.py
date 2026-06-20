import logging
import sys
import os

# Configure basic logging so we can see the output
logging.basicConfig(level=logging.INFO, format='%(levelname)s | %(name)s | %(message)s')

from ai_validator import AIValidator
from decision_engine import Decision
from trend_engine import TrendSignal
from config import config

def test():
    # Force enable for testing
    config.ai.enabled = True
    config.ai.validate_sells = True
    
    # Check if model exists, if not bootstrap train it
    model_path = os.path.join("data", "ml_validator_model.pkl")
    if not os.path.exists(model_path):
        print("XGBoost model not found. Bootstrapping training...")
        from ml_trainer import train_model
        train_model()
        
    validator = AIValidator()
    if validator.model is None:
        print("ERROR: AIValidator model could not be loaded.")
        return
    
    # Create a fake BUY decision
    fake_decision = Decision(
        action="BUY",
        confidence=0.95,
        reason="Test BUY signal based on strong momentum.",
        quantity=10,
        stop_loss_price=2450.0,
        take_profit_price=2600.0,
        combined_score=0.85
    )
    
    fake_trend = TrendSignal(
        symbol="RELIANCE.NS",
        rsi=35.0,
        ema_signal="bullish",
        macd_signal="bullish",
        atr=25.5,
        vwap_signal="above",
        overall_trend=0.8,
        current_price=2500.0
    )
    
    print("\n--- Testing AI Validator ---")
    print(f"Model loaded: {validator.model_path}")
    print("Sending fake BUY decision to XGBoost Model...\n")
    
    result = validator.validate_decision(
        symbol="RELIANCE.NS",
        trend_signal=fake_trend,
        sentiment_score=0.7,
        decision=fake_decision
    )
    
    print("\n--- Result ---")
    print(f"Final Action: {result.action}")
    print(f"Reasoning: {result.reason}")
    if hasattr(result, 'ai_decision'):
        print(f"AI Decision: {result.ai_decision}")
        print(f"AI Reason: {result.ai_reason}")

if __name__ == "__main__":
    test()
