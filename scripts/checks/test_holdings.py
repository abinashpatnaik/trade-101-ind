import sys
sys.path.append("/Users/abinash/Documents/untitled folder/trade-101-ind")
from zerodha_connector import ZerodhaConnector
z = ZerodhaConnector()
if z.is_authenticated():
    h = z.kite.holdings()
    if h:
        print(h[0])
    else:
        print("No holdings")
