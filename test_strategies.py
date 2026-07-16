import math

trades = [
    ("AAPL",   315.06, 311.89, 0.0496, -0.08, "STOP_LOSS",     "09:30", "09:37"),
    ("ADBE1",  218.12, 222.32, 0.0716, +0.04, "TRAILING_STOP", "09:30", "09:37"),
    ("AMGN1",  366.44, 372.91, 0.0426, -0.06, "TRAILING_STOP", "09:30", "09:36"),
    ("AMZN",   246.63, 247.62, 0.0633, +0.08, "TRAILING_STOP", "09:36", "09:58"),
    ("CMCSA",   23.89,  24.02, 0.651,  +0.05, "TRAILING_STOP", "09:37", "10:00"),
    ("COST1",  963.67, 960.09, 0.0161, -0.04, "TRAILING_STOP", "09:37", "10:30"),
    ("GOOGL",  371.75, 368.91, 0.042,  -0.08, "TRAILING_STOP", "09:58", "10:42"),
    ("INTU1",  275.86, 281.10, 0.0566, +0.25, "TRAILING_STOP", "10:01", "10:54"),
    ("ISRG1",  439.08, 439.49, 0.0356, -0.01, "TRAILING_STOP", "10:30", "10:50"),
    ("TXN1",   288.25, 291.15, 0.0542, +0.14, "TRAILING_STOP", "11:12", "12:08"),
    ("COST2",  956.15, 946.86, 0.0164, -0.15, "TRAILING_STOP", "11:06", "12:21"),
    ("ADBE3",  225.59, 229.45, 0.0692, +0.26, "TRAILING_STOP", "11:15", "12:13"),
    ("MELI",  1815.00,1820.27, 0.01,   +0.04, "TRAILING_STOP", "12:08", "14:49"),
    ("CRWD",   199.79, 198.40, 0.0785, -0.10, "TRAILING_STOP", "12:22", "12:58"),
    ("TXN2",   293.00, 293.71, 0.0535, +0.03, "TRAILING_STOP", "12:58", "13:16"),
    ("ADBE2",  228.24, 226.63, 0.0687, -0.10, "TRAILING_STOP", "13:16", "14:11"),
    ("INTU2",  282.15, 282.25, 0.0557, +0.00, "EOD",           "12:13", "15:45"),
    ("ISRG2",  426.18, 427.48, 0.0368, +0.05, "EOD",           "14:11", "15:45"),
    ("AMGN2",  367.04, 368.23, 0.0426, +0.06, "EOD",           "14:49", "15:45"),
]

def simulate():
    # 1. Reverse engineer Highs from old system
    sim_data = []
    for sym, buy, sell, qty, old_pnl, old_reason, bt, st in trades:
        high = sell
        if old_reason == "TRAILING_STOP":
            h1 = sell / 0.995
            gain1 = (h1 / buy) - 1
            if gain1 >= 0.005:
                high = h1
            else:
                disc = (0.01 * buy)**2 + 4 * sell * buy
                high = (0.01 * buy + math.sqrt(disc)) / 2
        elif old_reason == "EOD":
            high = max(buy, sell)
        
        sim_data.append({
            "sym": sym, "buy": buy, "sell": sell, "qty": qty, "old_pnl": old_pnl, 
            "old_reason": old_reason, "high": high
        })

    def run_strategy(name, calc_trigger_fn):
        total_pnl = 0.0
        for d in sim_data:
            buy = d["buy"]
            qty = d["qty"]
            high = d["high"]
            trigger = calc_trigger_fn(buy, high)
            
            # Did it hit the trigger before high?
            # We assume price went from buy -> high -> trigger/sell.
            # If trigger > high, it triggered immediately.
            # But trigger is based on high. So trigger is always <= high.
            # The question is: did price drop to trigger?
            # In old system, price dropped to old_sell.
            # If our new trigger is >= old_sell, it triggers at new trigger!
            # If our new trigger is < old_sell, it doesn't trigger! It holds to EOD (or hits hard stop if it dropped below buy).
            
            pnl = 0.0
            if trigger >= d["sell"]:
                # Triggered!
                pnl = round((trigger - buy) * qty, 2)
            else:
                # Held longer. Let's assume it finishes at EOD close.
                # If we don't have EOD close, we just use old_sell (conservative).
                pnl = round((d["sell"] - buy) * qty, 2)
                
            total_pnl += pnl
        return total_pnl

    print("Old System Total: ", sum(d["old_pnl"] for d in sim_data))

    # Strat 1: Continuous trailing stop (1% gap, tightening) - basically old system
    def strat_old(buy, high):
        gain = high/buy - 1
        pct = max(0.005, 0.01 - gain) if gain > 0 else 0.01
        return max(buy * 0.99, high * (1 - pct))
    print("Sim Old System Total:", run_strategy("Old", strat_old))

    # Strat 2: 3-Phase (1.5x arming) - the one I just committed
    def strat_3phase(buy, high):
        gap = 0.01
        # Hard stop / Breakeven
        effective_stop = buy * 0.99
        if high >= buy * (1 + gap):
            effective_stop = max(effective_stop, buy)
        
        # Trailing
        trailing_trigger = 0
        if high >= buy * (1 + 1.5 * gap):
            gain = high/buy - 1
            pct = max(0.005, 0.01 - gain)
            trailing_trigger = high * (1 - pct)
            
        return max(effective_stop, trailing_trigger)
    print("3-Phase (1.5x arm) Total:", run_strategy("3Phase1.5", strat_3phase))

    # Strat 3: Continuous Trailing, but break-even immediately at +0.5%
    def strat_fast_be(buy, high):
        gap = 0.01
        gain = high/buy - 1
        pct = gap
        trigger = high * (1 - pct)
        if high >= buy * 1.005:
            trigger = max(trigger, buy) # break-even lock
        return max(buy * 0.99, trigger)
    print("Fast Break-even (+0.5%) Total:", run_strategy("Fast BE", strat_fast_be))

    # Strat 4: Trailing gap is 0.5%
    def strat_tight(buy, high):
        gap = 0.005
        trigger = high * (1 - gap)
        return max(buy * 0.99, trigger)
    print("Tight gap (0.5%) Total:", run_strategy("Tight Gap", strat_tight))

simulate()
