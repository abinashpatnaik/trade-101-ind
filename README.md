# Trading Agent

The Python trading agent that runs on AWS EC2 and autonomously trades 20 FTSE 100 stocks during LSE market hours.

## File Structure

| File | Purpose |
|------|---------|
| `agent.py` | Main loop — orchestrates the 60-second scan cycle, scheduling, and shutdown |
| `config.py` | All configuration parameters loaded from environment variables |
| `ibkr_connector.py` | HTTP client for the IBKR Client Portal REST API |
| `market_session.py` | Market hours detection (LSE 08:00–16:30 BST, Mon–Fri) |
| `price_feed.py` | Fetches OHLCV price data via yfinance and IBKR market data |
| `trend_engine.py` | Computes technical indicators: RSI, EMA crossover, MACD, ATR, VWAP |
| `sentiment_engine.py` | Fetches and scores RSS news headlines from BBC/Reuters/FT |
| `decision_engine.py` | Aggregates signals into a combined score, applies BUY/SELL thresholds |
| `order_executor.py` | Places, monitors, and cancels orders via IBKR CP API |
| `portfolio_tracker.py` | Tracks open positions, stop levels, P&L; writes `trades.csv` |
| `report_generator.py` | Compiles end-of-day HTML/text report from trades and portfolio data |
| `report_sender.py` | Sends EOD report via Gmail SMTP |
| `Dockerfile` | Container image definition for the trading agent |
| `docker-compose.yml` | Full stack: ibeam + trading agent + dashboard |
| `.env.example` | Template for environment variables — copy to `.env` and fill in |
| `setup_vm.sh` | One-command EC2 setup script (installs Docker, clones repo, etc.) |
| `CLOUD_DEPLOYMENT.md` | Full step-by-step AWS deployment guide |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in all values before starting.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `IBKR_USERNAME` | ✅ | — | Your IBKR account username |
| `IBKR_PASSWORD` | ✅ | — | Your IBKR account password |
| `IBKR_2FA_KEY` | ❌ | — | TOTP secret for automated 2FA (recommended) |
| `TRADING_MODE` | ✅ | `paper` | `paper` or `live` |
| `IBKR_PORT` | ❌ | `4002` | CP Gateway port (4002=paper, 4001=live) |
| `IBKR_GATEWAY_URL` | ❌ | `https://localhost:5000` | URL of IBeam-managed gateway |
| `EOD_API_KEY` | ❌ | — | EOD Historical Data API key (free tier available) |
| `GMAIL_ADDRESS` | ✅ | — | Gmail address for sending EOD reports |
| `GMAIL_APP_PASSWORD` | ✅ | — | Gmail App Password (not your account password) |
| `REPORT_RECIPIENT` | ✅ | — | Email address to receive daily reports |

### Getting a Gmail App Password

1. Enable 2-Step Verification on your Google account
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Create a new app password named "FTSE Trading Agent"
4. Copy the 16-character password into `GMAIL_APP_PASSWORD`

---

## Running Locally (Without Docker)

Useful for development and debugging.

### Prerequisites

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Start IBeam separately

The agent needs an authenticated IBKR gateway. On a local machine, start IBeam via Docker:

```bash
docker run -d \
  -p 5000:5000 \
  -e IBEAM_ACCOUNT=your_username \
  -e IBEAM_PASSWORD=your_password \
  --name ibeam \
  voyz/ibeam:latest
```

Wait ~60 seconds for authentication, then verify:

```bash
curl -k https://localhost:5000/v1/api/iserver/auth/status
```

### Run the agent

```bash
cd agent/
cp .env.example .env
# Edit .env with your credentials
export $(cat .env | xargs)
python agent.py
```

The agent will log to console and write `trades.csv` in the current directory.

---

## Running With Docker Compose (Recommended)

```bash
cd agent/
cp .env.example .env
# Edit .env with your credentials

docker-compose up -d

# View logs
docker-compose logs -f agent

# View dashboard at http://localhost:3000

# Stop everything
docker-compose down
```

### Docker Compose Services

| Service | Container | Port |
|---------|-----------|------|
| `ibeam` | IBKR CP Gateway + auth | 5000 (internal) |
| `agent` | Python trading agent | — |
| `dashboard` | Node.js web UI | 3000 |

---

## Configuration Options

All parameters are in `config.py`. Key settings:

```python
# Trading universe — 20 FTSE 100 stocks
SYMBOLS = ["HSBA", "AZN", "SHEL", "RIO", "BP", "ULVR", "GSK", "BATS",
           "REL", "PRU", "LGEN", "NG", "VOD", "BT.A", "LLOY",
           "BARC", "NWG", "RKT", "DGE", "IMB"]

# Signal thresholds
BUY_THRESHOLD = 0.35
SELL_THRESHOLD = -0.35

# Risk controls
TRAILING_STOP_PCT = 0.015     # 1.5%
FIXED_STOP_LOSS_PCT = 0.02    # 2%
TAKE_PROFIT_PCT = 0.04        # 4%
DAILY_LOSS_CAP_PCT = 0.02     # 2%
MAX_POSITIONS = 10
MAX_POSITION_SIZE_PCT = 0.05  # 5% of NAV

# Market hours
MARKET_OPEN = "08:00"
MARKET_CLOSE = "16:30"
EOD_CLOSE_TIME = "16:10"      # Start closing positions
REPORT_TIME = "16:45"         # Send daily email report

# Scan interval
SCAN_INTERVAL_SECONDS = 60
```

---

## Logs and Data

| File | Location | Description |
|------|----------|-------------|
| Agent log | `./logs/agent.log` | All agent activity with timestamps |
| Trades | `./data/trades.csv` | All executed trades (date, symbol, action, price, qty, P&L) |

`trades.csv` format:
```
date,symbol,action,price,quantity,value,pnl,reason
2026-01-15,HSBA,BUY,750.20,13,9752.60,,signal
2026-01-15,HSBA,SELL,771.80,13,10033.40,280.80,take_profit
```
# CI/CD pipeline active — 2026-06-09
# SSH key fix test — 2026-06-09T14:49:03Z
# Deploy test — 2026-06-09T14:54:24Z
# Pipeline test — 2026-06-09T15:37:47Z
