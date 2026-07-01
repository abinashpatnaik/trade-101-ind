import os
import time
import pyotp
from dotenv import load_dotenv
from kiteconnect import KiteConnect

def test_zerodha():
    # Load .env file
    load_dotenv()
    
    api_key = os.getenv("KITE_API_KEY")
    api_secret = os.getenv("KITE_API_SECRET")
    user_id = os.getenv("KITE_USER_ID")
    password = os.getenv("KITE_PASSWORD")
    totp_secret = os.getenv("KITE_TOTP_SECRET")
    
    if not all([api_key, api_secret, user_id, password, totp_secret]):
        print("❌ Error: Missing Zerodha credentials in .env file.")
        print("Make sure you have: KITE_API_KEY, KITE_API_SECRET, KITE_USER_ID, KITE_PASSWORD, KITE_TOTP_SECRET")
        return
        
    print(f"✅ Credentials loaded for user: {user_id}")
    print("🔄 Initializing KiteConnect and fetching Request Token...")
    
    try:
        from zerodha_connector import ZerodhaConnector
        # We can use our own ZerodhaConnector to test if the login flow works
        connector = ZerodhaConnector()
        success = connector.connect()
        
        if success:
            print("🎉 SUCCESS! Successfully connected and authenticated with Zerodha.")
            
            # Let's try to fetch a live quote to be absolutely sure
            print("\n📈 Fetching live quote for RELIANCE.NS...")
            price = connector.get_current_price("RELIANCE.NS")
            if price:
                print(f"✅ Live Price of RELIANCE: ₹{price}")
            else:
                print("⚠️ Connected successfully, but failed to fetch live price.")
                
        else:
            print("❌ FAILED to connect. Check your credentials and TOTP secret.")
            
    except Exception as e:
        print(f"❌ Exception occurred during test: {e}")

if __name__ == "__main__":
    test_zerodha()
