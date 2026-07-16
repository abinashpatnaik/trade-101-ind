import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

load_dotenv()
trading_client = TradingClient(os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY"), paper=True)
account = trading_client.get_account()
print(f"portfolio_value: {account.portfolio_value}")
print(f"cash: {account.cash}")
print(f"buying_power: {account.buying_power}")
print(f"equity: {account.equity}")
print(f"last_equity: {account.last_equity}")
print(f"multiplier: {account.multiplier}")
