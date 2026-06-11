import sys
sys.path.insert(0, '.')

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from zerodha_connector import ZerodhaConnector

try:
    connector = ZerodhaConnector()
    print("Connector instantiated successfully.")
    print(f"API Key loaded: {'Yes' if connector.api_key else 'No'}")
    print(f"User ID loaded: {'Yes' if connector.user_id else 'No'}")
    print(f"Password loaded: {'Yes' if connector.password else 'No'}")
    print(f"TOTP Secret loaded: {'Yes' if connector.totp_secret else 'No'}")
except Exception as e:
    print(f"Error: {e}")
