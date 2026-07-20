# 🇮🇳 Trade Simulation: Indian Market Analysis

**Observations:**
- **Multiple Shutdowns:** The agent was stopped and restarted 3 times within 15 minutes (between 10:58 and 11:08). This triggered `LIQUIDATE_ON_SHUTDOWN` and forced premature exits.
- **Trailing Stop Trigger:** Only one trade (`BHARTIARTL.NS`) survived long enough to hit a trailing stop naturally.
- **EOD Exits:** The rest of the trades held until End of Day (15:15) where they were forcibly closed.

### 📉 Trades affected by the new Trailing Stop
| Symbol | Entry | High Reached | New Exit | PnL | Label |
|--------|-------|--------------|----------|-----|-------|
| BHARTIARTL.NS_1 | ₹1945.20 | ₹1945.78 (+0.03%) | ₹1926.90 | **₹-18.40** | `STOP_LOSS` |

### 💰 Final Results (Including Shutdowns/EOD)
- **Old System Total PnL:** `₹-34.14`
- **New System Total PnL:** `₹-34.14`
- **Improvement:** `₹+0.00`