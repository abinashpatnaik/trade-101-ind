import urllib.request
import json
import datetime
import os

env_path = ".env"
keys = {}
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            if line.strip() and not line.startswith('#'):
                parts = line.strip().split('=', 1)
                if len(parts) == 2:
                    keys[parts[0]] = parts[1]

API_KEY = keys.get("APCA_API_KEY_ID")
API_SECRET = keys.get("APCA_API_SECRET_KEY")

headers = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Content-Type": "application/json"
}

BASE_URL = "https://api.alpaca.markets"

today = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')
url = f"{BASE_URL}/v2/orders?status=all&after={today}&direction=asc"

req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req) as response:
        orders = json.loads(response.read().decode())
        print(f"Found {len(orders)} orders today.")
        for o in orders:
            # Check if this order is from today
            print(f"[{o['submitted_at']}] {o['side'].upper()} {o['symbol']} | status: {o['status']} | qty: {o['qty']} | type: {o['type']} | limit: {o.get('limit_price')} | stop: {o.get('stop_price')} | filled_avg: {o.get('filled_avg_price')} | id: {o['id']}")
except Exception as e:
    print(f"Error fetching orders: {e}")
    if hasattr(e, 'read'):
        print(e.read().decode())

