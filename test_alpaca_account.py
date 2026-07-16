import os
import sys

sys.path.append("/Users/abinash/Documents/untitled folder/trade-101-ind")

env_vars = {}
with open(".env", "r") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            if "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()

from alpaca.trading.client import TradingClient

api_key = env_vars.get("APCA_API_KEY_ID")
api_secret = env_vars.get("APCA_API_SECRET_KEY")

if not api_key:
    print("No API Key")
    sys.exit(1)

client = TradingClient(api_key, api_secret, paper=True)
account = client.get_account()
print(f"Cash: {account.cash}")
print(f"Portfolio Value: {account.portfolio_value}")
print(f"Equity: {account.equity}")
print(f"Buying Power: {account.buying_power}")
