import math

# Date	Time	Symbol	Action	Qty	Price	P&L	Exit Reason
# 2026-07-07	15:15:39	ITC.NS	SELL	10.0	288.90	+11.83	EOD
# 2026-07-07	15:15:38	AXISBANK.NS	SELL	2.0	1340.20	-13.40	EOD
# 2026-07-07	15:15:37	ASIANPAINT.NS	SELL	1.0	2730.30	-15.57	EOD
# 2026-07-07	13:33:21	ASIANPAINT.NS	BUY	1.0	2739.30	—	None
# 2026-07-07	13:33:05	BHARTIARTL.NS	SELL	1.0	1926.90	-18.40	TRAILING_STOP
# 2026-07-07	11:09:34	ITC.NS	BUY	10.0	288.05	—	None
# 2026-07-07	11:09:15	BHARTIARTL.NS	BUY	1.0	1945.20	—	None
# 2026-07-07	11:09:09	AXISBANK.NS	BUY	2.0	1346.40	—	None
# 2026-07-07	11:08:24	RELIANCE.NS	SELL	2.0	1318.50	-2.40	SHUTDOWN
# 2026-07-07	11:08:23	AXISBANK.NS	SELL	2.0	1346.30	-0.60	SHUTDOWN
# 2026-07-07	11:01:45	RELIANCE.NS	BUY	2.0	1319.60	—	None
# 2026-07-07	11:01:07	AXISBANK.NS	BUY	2.0	1346.70	—	None
# 2026-07-07	11:00:41	ITC.NS	SELL	10.0	287.85	+3.00	SHUTDOWN
# 2026-07-07	11:00:40	ASIANPAINT.NS	SELL	1.0	2750.60	+1.50	SHUTDOWN
# 2026-07-07	10:59:29	ITC.NS	BUY	10.0	287.45	—	None
# 2026-07-07	10:59:08	ASIANPAINT.NS	BUY	1.0	2749.30	—	None
# 2026-07-07	10:58:44	ITC.NS	SELL	10.0	287.45	-0.50	SHUTDOWN
# 2026-07-07	10:58:43	ASIANPAINT.NS	SELL	1.0	2749.30	+0.40	SHUTDOWN
# 2026-07-07	10:58:15	ITC.NS	BUY	10.0	287.50	—	None
# 2026-07-07	10:57:38	ASIANPAINT.NS	BUY	1.0	2748.90	—	None

trades = [
    ("ASIANPAINT.NS_1", 2748.90, 2749.30, 1.0, 0.40, "SHUTDOWN"),
    ("ITC.NS_1", 287.50, 287.45, 10.0, -0.50, "SHUTDOWN"),
    ("ASIANPAINT.NS_2", 2749.30, 2750.60, 1.0, 1.50, "SHUTDOWN"),
    ("ITC.NS_2", 287.45, 287.85, 10.0, 3.00, "SHUTDOWN"),
    ("AXISBANK.NS_1", 1346.70, 1346.30, 2.0, -0.60, "SHUTDOWN"),
    ("RELIANCE.NS_1", 1319.60, 1318.50, 2.0, -2.40, "SHUTDOWN"),
    ("BHARTIARTL.NS_1", 1945.20, 1926.90, 1.0, -18.40, "TRAILING_STOP"),
    ("ASIANPAINT.NS_3", 2739.30, 2730.30, 1.0, -15.57, "EOD"), # NOTE: 2730.30 - 2739.30 = -9. Pnl is -15.57. Maybe slippage or fees?
    ("AXISBANK.NS_2", 1346.40, 1340.20, 2.0, -13.40, "EOD"), # 1340.20 - 1346.40 = -6.2 * 2 = -12.4. Diff is -1.
    ("ITC.NS_3", 288.05, 288.90, 10.0, 11.83, "EOD"), # 288.90 - 288.05 = 0.85 * 10 = +8.5. Wait PnL is +11.83? M2M mismatch.
]

output = []
output.append("# 🇮🇳 Trade Simulation: Indian Market Analysis")
output.append("\n**Observations:**")
output.append("- **Multiple Shutdowns:** The agent was stopped and restarted 3 times within 15 minutes (between 10:58 and 11:08). This triggered `LIQUIDATE_ON_SHUTDOWN` and forced premature exits.")
output.append("- **Trailing Stop Trigger:** Only one trade (`BHARTIARTL.NS`) survived long enough to hit a trailing stop naturally.")
output.append("- **EOD Exits:** The rest of the trades held until End of Day (15:15) where they were forcibly closed.")

output.append("\n### 📉 Trades affected by the new Trailing Stop")
output.append("| Symbol | Entry | High Reached | New Exit | PnL | Label |")
output.append("|--------|-------|--------------|----------|-----|-------|")

old_total = sum(t[4] for t in trades)
new_total = 0.0

winners = []
losers = []

for sym, buy, sell, qty, old_pnl, old_reason in trades:
    # 1. Reverse engineer Highs from old system
    high = max(buy, sell)
    if old_reason == "TRAILING_STOP":
        h1 = sell / 0.995
        gain1 = (h1 / buy) - 1
        if gain1 >= 0.005:
            high = h1
        else:
            disc = (0.01 * buy)**2 + 4 * sell * buy
            high = (0.01 * buy + math.sqrt(disc)) / 2
            
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
    if old_reason in ("SHUTDOWN", "EOD"):
        new_exit = sell
        new_reason = old_reason
    elif trigger >= sell:
        new_exit = trigger
        new_reason = "TRAILING_STOP" if trigger >= buy else "STOP_LOSS"
    else:
        new_exit = max(sell, trigger)
        new_reason = "TRAILING_STOP" if new_exit >= buy else "STOP_LOSS"
        
    # Recalculate PnL (approximate with diff + old_pnl to preserve fees)
    diff_from_old = (new_exit - sell) * qty
    new_pnl = round(old_pnl + diff_from_old, 2)
    new_total += new_pnl
    
    row = f"| {sym} | ₹{buy:.2f} | ₹{high:.2f} (+{gain_pct*100:.2f}%) | ₹{new_exit:.2f} | **₹{new_pnl:+.2f}** | `{new_reason}` |"
    
    if new_reason not in ("SHUTDOWN", "EOD"):
        if new_pnl >= 0:
            winners.append(row)
        else:
            losers.append(row)

for row in losers:
    output.append(row)
for row in winners:
    output.append(row)

if not losers and not winners:
    output.append("| (No trailing stops) | - | - | - | - | - |")

output.append("\n### 💰 Final Results (Including Shutdowns/EOD)")
output.append(f"- **Old System Total PnL:** `₹{old_total:+.2f}`")
output.append(f"- **New System Total PnL:** `₹{new_total:+.2f}`")
improvement = new_total - old_total
output.append(f"- **Improvement:** `₹{improvement:+.2f}`")

with open("trade_simulation_india.md", "w") as f:
    f.write("\n".join(output))

