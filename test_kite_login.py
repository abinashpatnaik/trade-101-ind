import requests
import pyotp
import urllib.parse
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("KITE_API_KEY")
user_id = os.getenv("KITE_USER_ID")
password = os.getenv("KITE_PASSWORD")
totp_secret = os.getenv("KITE_TOTP_SECRET")

print("Logging into Kite...")
session = requests.Session()
login_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
session.get(login_url)

login_payload = {"user_id": user_id, "password": password}
login_resp = session.post("https://kite.zerodha.com/api/login", data=login_payload)
print("Login Resp:", login_resp.json())

login_data = login_resp.json()
request_id = login_data["data"]["request_id"]

totp = pyotp.TOTP(totp_secret).now()
twofa_payload = {"user_id": user_id, "request_id": request_id, "twofa_value": totp, "twofa_type": "totp"}
twofa_resp = session.post("https://kite.zerodha.com/api/twofa", data=twofa_payload)
print("2FA Resp:", twofa_resp.json())

login_url_with_skip = login_url + "&skip_session=true"
try:
    redirect_resp = session.get(login_url_with_skip, allow_redirects=True)
    redirect_url = redirect_resp.url
    print("Redirect URL:", redirect_url)
except requests.exceptions.ConnectionError as e:
    if e.request:
        redirect_url = e.request.url
        print("Redirect URL from exception:", redirect_url)
    else:
        print("ConnectionError without request URL")
