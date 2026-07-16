import sys
import pandas as pd
from price_feed import PriceFeed
from config import load_config

def main():
    cfg = load_config()
    feed = PriceFeed(cfg)
    
    trades = [
        ("AAPL",   315.06, 0.0496),
        ("ADBE",   218.12, 0.0716),
        ("AMGN",   366.44, 0.0426),
        ("AMZN",   246.63, 0.0633),
        ("CMCSA",   23.89, 0.651),
        ("COST",   963.67, 0.0161),
        ("GOOGL",  371.75, 0.042),
        ("INTU",   275.86, 0.0566),
        ("ISRG",   439.08, 0.0356),
        ("TXN",    288.25, 0.0542),
        ("CRWD",   199.79, 0.0785),
        ("MELI",  1815.00, 0.01)
    ]
    
    print("Fetching 1m data for simulation...")
    results = []
    
    for sym, buy, qty in trades:
        df = feed.get_intraday_data(sym, days=2)
        if df is None or df.empty:
            print(f"Skipping {sym} (no data)")
            continue
            
        # Filter to just yesterday
        df = df[df.index.date == df.index[-1].date()]
        
        # Start looking after the buy price is hit
        # (This is approximate since we don't have exact trade times matched to the index)
        # We will just simulate from the first time it crosses `buy` price.
        crossed = df[df['High'] >= buy]
        if crossed.empty:
            continue
        
        start_idx = crossed.index[0]
        df_sim = df.loc[start_idx:]
        
        # Variables for simulation
        high = buy
        old_trigger = 0.0
        new_trigger = 0.0
        
        old_exit_price = None
        new_exit_price = None
        
        gap = 0.01
        
        for idx, row in df_sim.iterrows():
            curr_high = row['High']
            curr_low = row['Low']
            
            if curr_high > high:
                high = curr_high
                
            # --- OLD SYSTEM ---
            if old_exit_price is None:
                # hard stop
                if curr_low <= buy * 0.99:
                    old_exit_price = buy * 0.99
                else:
                    gain = high / buy - 1
                    pct = max(0.005, 0.01 - gain) if gain > 0 else 0.01
                    trigger = high * (1 - pct)
                    if curr_low <= trigger:
                        old_exit_price = trigger
            
            # --- NEW SYSTEM (3-Phase 1.5x) ---
            if new_exit_price is None:
                # Phase 1
                effective_stop = buy * 0.99
                # Phase 2
                if high >= buy * (1 + gap):
                    effective_stop = max(effective_stop, buy)
                
                # Phase 3
                trailing_trigger = 0.0
                if high >= buy * (1 + 1.5 * gap):
                    gain = high / buy - 1
                    pct = max(0.005, 0.01 - gain)
                    trailing_trigger = high * (1 - pct)
                
                final_trigger = max(effective_stop, trailing_trigger)
                if curr_low <= final_trigger:
                    new_exit_price = final_trigger

        if old_exit_price is None:
            old_exit_price = df_sim.iloc[-1]['Close']
        if new_exit_price is None:
            new_exit_price = df_sim.iloc[-1]['Close']
            
        old_pnl = (old_exit_price - buy) * qty
        new_pnl = (new_exit_price - buy) * qty
        
        results.append((sym, old_pnl, new_pnl))
        print(f"{sym}: Old={old_pnl:+.2f}, New={new_pnl:+.2f}")
        
    print("-" * 30)
    print(f"Old Total: {sum(r[1] for r in results):+.2f}")
    print(f"New Total: {sum(r[2] for r in results):+.2f}")

if __name__ == '__main__':
    main()
