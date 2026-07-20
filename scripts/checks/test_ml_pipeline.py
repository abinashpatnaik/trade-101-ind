import logging
import pandas as pd
from continuous_learning import ContinuousLearning
from trend_engine import TrendSignal
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_test():
    logger.info("Initializing Continuous Learning module...")
    cl = ContinuousLearning()
    
    logger.info(f"Target log path: {cl.features_log_path}")
    
    # 1. Create a dummy trend signal
    mock_trend = TrendSignal(
        symbol="TEST-SYM",
        current_price=100.0,
        rsi=45.2,
        macd_signal=1,
        ema_signal=-1,
        vwap_signal=1,
        overall_trend=0.33,
        atr=1.2
    )
    
    # 2. Log some mock features with scraped news sentiment
    logger.info("Simulating an AI validation and logging features...")
    cl.log_daily_features("TEST-SYM", mock_trend, sentiment_score=0.75)
    
    # 3. Read the file to prove it worked
    try:
        df = pd.read_csv(cl.features_log_path)
        logger.info("\n--- CONTENTS OF training_features.csv ---")
        print(df.tail())
        logger.info("-----------------------------------------")
        logger.info("Success! The CSV generation and logging pipeline is working perfectly.")
    except Exception as e:
        logger.error(f"Failed to read CSV: {e}")

if __name__ == "__main__":
    run_test()
