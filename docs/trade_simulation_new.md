# 📊 Trade Simulation: Continuous Fast Break-Even Strategy

**How it works:**
- Trailing stop is **active immediately** to cut losses early if a stock drops.
- It tightens dynamically as the stock rises.
- **Fast Break-Even:** If the stock rises by just +0.5%, the trigger is floored at the entry price (guaranteeing $0 loss).
- If it exits below entry price, it logs as `STOP_LOSS` to avoid confusion. If above, it's a `TRAILING_STOP`.

### 📉 Stocks that dropped (or barely rose) and hit Stop Loss
| Symbol | Entry | High Reached | New Exit | PnL | Label |
|--------|-------|--------------|----------|-----|-------|
| AAPL | $315.06 | $311.89 (+-1.01%) | $311.89 | **$-0.16** | `STOP_LOSS` |
| COST1 | $963.67 | $966.71 (+0.32%) | $960.09 | **$-0.06** | `STOP_LOSS` |
| GOOGL | $371.75 | $372.19 (+0.12%) | $368.91 | **$-0.12** | `STOP_LOSS` |
| COST2 | $956.15 | $956.29 (+0.01%) | $946.86 | **$-0.15** | `STOP_LOSS` |
| CRWD | $199.79 | $200.10 (+0.15%) | $198.40 | **$-0.11** | `STOP_LOSS` |
| ADBE2 | $228.24 | $228.58 (+0.15%) | $226.63 | **$-0.11** | `STOP_LOSS` |

### 📈 Stocks that locked in Profit or Break-Even
| Symbol | Entry | High Reached | New Exit | PnL | Label |
|--------|-------|--------------|----------|-----|-------|
| ADBE1 | $218.12 | $223.44 (+2.44%) | $222.32 | **$+0.30** | `TRAILING_STOP` |
| AMGN1 | $366.44 | $374.78 (+2.28%) | $372.91 | **$+0.28** | `TRAILING_STOP` |
| AMZN | $246.63 | $248.86 (+0.91%) | $247.62 | **$+0.06** | `TRAILING_STOP` |
| CMCSA | $23.89 | $24.14 (+1.05%) | $24.02 | **$+0.08** | `TRAILING_STOP` |
| INTU1 | $275.86 | $282.51 (+2.41%) | $281.10 | **$+0.30** | `TRAILING_STOP` |
| ISRG1 | $439.08 | $441.70 (+0.60%) | $439.49 | **$+0.01** | `TRAILING_STOP` |
| TXN1 | $288.25 | $292.61 (+1.51%) | $291.15 | **$+0.16** | `TRAILING_STOP` |
| ADBE3 | $225.59 | $230.60 (+2.22%) | $229.45 | **$+0.27** | `TRAILING_STOP` |
| MELI | $1815.00 | $1829.42 (+0.79%) | $1820.27 | **$+0.05** | `TRAILING_STOP` |
| TXN2 | $293.00 | $295.19 (+0.75%) | $293.71 | **$+0.04** | `TRAILING_STOP` |
| INTU2 | $282.15 | $282.25 (+0.04%) | $282.25 | **$+0.01** | `EOD` |
| ISRG2 | $426.18 | $427.48 (+0.31%) | $427.48 | **$+0.05** | `EOD` |
| AMGN2 | $367.04 | $368.23 (+0.32%) | $368.23 | **$+0.05** | `EOD` |

### 💰 Final Results
- **Old System Total PnL:** `$+0.38`
- **New System Total PnL:** `$+0.95`
- **Improvement:** `$+0.57`