import sys
sys.path.insert(0, '.')

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from sentiment_engine import SentimentEngine

engine = SentimentEngine()
score = engine.get_sentiment('RELIANCE.NS')
print(f"RELIANCE.NS sentiment: {score:.3f}")
headlines = engine.get_news_headlines('RELIANCE.NS')
print(f"Headlines: {headlines}")
