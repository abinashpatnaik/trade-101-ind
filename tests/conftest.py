import os
import sys

# Make the repo root importable and pin the market before config import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TRADING_MARKET", "IN")
os.environ.setdefault("REDIS_URL", "redis://localhost:6399/0")  # deliberately unused port
