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

output = []
output.append("# 📊 Trade Simulation: Continuous Fast Break-Even Strategy")
output.append("\n**How it works:**")
output.append("- Trailing stop is **active immediately** to cut losses early if a stock drops.")
output.append("- It tightens dynamically as the stock rises.")
output.append("- **Fast Break-Even:** If the stock rises by just +0.5%, the trigger is floored at the entry price (guaranteeing $0 loss).")
output.append("- If it exits below entry price, it logs as `STOP_LOSS` to avoid confusion. If above, it's a `TRAILING_STOP`.")
output.append("\n### 📉 Stocks that dropped (or barely rose) and hit Stop Loss")
output.append("| Symbol | Entry | High Reached | New Exit | PnL | Label |")
output.append("|--------|-------|--------------|----------|-----|-------|")

old_total = 0.0
new_total = 0.0

winners = []
losers = []

for sym, buy, sell, qty, old_pnl, old_reason, bt, st in trades:
    old_total += old_pnl
    
    # 1. Reverse engineer Highs from old system
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
        
    gain_pct = high / buy - 1
    
    # 2. Apply New Logic
    if gain_pct > 0:
        current_trailing_pct = max(0.005, 0.01 - gain_pct)
    else:
        current_trailing_pct = 0.01
        
    trigger = high * (1.0 - current_trailing_pct)
    
    if gain_pct >= 0.005:
        trigger = max(trigger, buy)
        
    # Did it trigger?
    # Because we reverted to the Continuous trailing stop, the trigger condition is almost 
    # identical to the old system. So if it triggered in the old system, it triggers in the new one.
    if old_reason == "EOD":
        new_exit = sell
        new_reason = "EOD"
    elif trigger >= sell:
        new_exit = trigger
        new_reason = "TRAILING_STOP" if trigger >= buy else "STOP_LOSS"
    else:
        # In reality this shouldn't happen much because the logic is the same, 
        # except when Break-Even floor raises the trigger!
        new_exit = max(sell, trigger)
        new_reason = "TRAILING_STOP" if new_exit >= buy else "STOP_LOSS"
        
    new_pnl = round((new_exit - buy) * qty, 2)
    new_total += new_pnl
    
    row = f"| {sym} | ${buy:.2f} | ${high:.2f} (+{gain_pct*100:.2f}%) | ${new_exit:.2f} | **${new_pnl:+.2f}** | `{new_reason}` |"
    
    if new_pnl >= 0:
        winners.append(row)
    else:
        losers.append(row)

for row in losers:
    output.append(row)
    
output.append("\n### 📈 Stocks that locked in Profit or Break-Even")
output.append("| Symbol | Entry | High Reached | New Exit | PnL | Label |")
output.append("|--------|-------|--------------|----------|-----|-------|")
for row in winners:
    output.append(row)

output.append("\n### 💰 Final Results")
output.append(f"- **Old System Total PnL:** `${old_total:+.2f}`")
output.append(f"- **New System Total PnL:** `${new_total:+.2f}`")
improvement = new_total - old_total
output.append(f"- **Improvement:** `${improvement:+.2f}`")

with open("trade_simulation_new.md", "w") as f:
    f.write("\n".join(output))

print("Simulation written to trade_simulation_new.md")
