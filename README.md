# NSE Nifty 50 Trading Agent

An autonomous Python trading agent designed to run on AWS EC2, trading a liquid universe of 20 Nifty 50 stocks during NSE market hours using the Zerodha Kite Connect API.

---

## 📁 File Structure

| File | Purpose |
|------|---------|
| `agent.py` | Main orchestrator — drives the scan cycle, session state gating, and graceful shutdown |
| `config.py` | Configuration settings loaded from environment variables |
| `zerodha_connector.py` | API client for Zerodha Kite Connect (automated TOTP login, access token caching, order execution, margins) |
| `market_session.py` | Market hours manager (NSE 09:15–15:30 IST, Mon–Fri) |
| `price_feed.py` | Fetches historical OHLCV data from Yahoo Finance (appending `.NS`) |
| `trend_engine.py` | Computes technical indicators: RSI, EMA crossover, MACD, ATR, and VWAP |
| `sentiment_engine.py` | Aggregates and scores RSS feeds (Moneycontrol, ET) and Google News queries |
| `decision_engine.py` | Evaluates composite trend + sentiment scores and executes BUY/SELL signals |
| `order_executor.py` | Interfaces with the broker client to place and track trades (MARKET, LIMIT, SL-M) |
| `portfolio_tracker.py` | Tracks capital, open positions, trailing stops, and writes local records |
| `report_generator.py` | Generates end-of-day HTML and plain-text summaries in Rupees (`₹`) |
| `report_sender.py` | Emails EOD reports via Gmail SMTP |
| `Dockerfile` | Standalone Docker container definition (running Python 3.11 on Asia/Kolkata timezone) |
| `docker-compose.yml` | Standalone service stack mapping persistent logs and cache volumes |
| `setup_vm.sh` | One-command Ubuntu 22.04 VM setup script (installs Docker and Docker Compose) |
| `CLOUD_DEPLOYMENT.md` | Full cloud deployment guide and CI/CD manual |

---

## 🔑 Environment Variables

Create a `.env` file in the project root containing the following parameters:

```ini
# Zerodha Kite Connect API
KITE_API_KEY=your_kite_api_key
KITE_API_SECRET=your_kite_api_secret

# Zerodha Credentials (for automated login)
KITE_USER_ID=your_client_id
KITE_PASSWORD=your_password
KITE_TOTP_SECRET=your_totp_setup_secret_key

# Email EOD Reports
GMAIL_ADDRESS=your@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
REPORT_RECIPIENT=your@gmail.com

# Trading Configuration
TRADING_MODE=paper
```

---

## 🚀 Running Locally (Without Docker)

1. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. **Install requirements**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Start the agent**:
   ```bash
   python agent.py
   ```

---

## 🐳 Running with Docker Compose

Build and start the standalone container service in the background:
```bash
docker-compose up -d --build

# Inspect running container logs
docker-compose logs -f trading-agent

# Stop the container
docker-compose down
```

---

## 🔄 Automated Deployment to EC2 via GitHub Actions

This project contains a CI/CD workflow defined in `.github/workflows/deploy.yml` that automates pushes directly to your AWS EC2 instance.

### Setup Instructions:
1. Ensure your EC2 VM is prepared by running `setup_vm.sh` on the instance.
2. In your GitHub repository, navigate to **Settings ➔ Secrets and variables ➔ Actions ➔ New repository secret** and add:
   *   `EC2_HOST`: Your EC2 public IP address (`16.171.65.174`).
   *   `EC2_USERNAME`: Your SSH username (`ec2-user`).
   *   `EC2_SSH_KEY`: The entire text contents of your private SSH key file (`.pem`).
3. Commit and push any changes to your `main` branch. GitHub Actions will handle copying files, establishing target directories, and rebuilding/restarting the trading container stack on your VM.
