# NSE Nifty 50 Trading Agent — Cloud Deployment Guide

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Cloud VM (Ubuntu 22.04)                  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                  Docker Container                        │   │
│  │                                                          │   │
│  │  ┌────────────────────────────────────────────────────┐  │   │
│  │  │               Trading Agent Container              │  │   │
│  │  │                 (python:3.11-slim)                 │  │   │
│  │  │                                                    │  │   │
│  │  │  - ZerodhaConnector (REST API, automated login)    │  │   │
│  │  │  - PriceFeed (Yahoo Finance historical data)       │  │   │
│  │  │  - TrendEngine (technical indicators)              │  │   │
│  │  │  - SentimentEngine (RSS & Google News feeds)       │  │   │
│  │  │  - DecisionEngine                                  │  │   │
│  │  │  - OrderExecutor (trades placed directly)          │  │   │
│  │  └────────┬───────────────────────────────────────────┘  │   │
│  └───────────┼──────────────────────────────────────────────┘   │
│              │                                                   │
└──────────────┼───────────────────────────────────────────────────┘
               │
               ▼ HTTPS / 443
       Zerodha API Gateway
      api.kite.trade (REST)
```

The trading agent operates as a standalone Dockerized service. Unlike the legacy setup, there is no requirement for a local gateway client (e.g., IBeam), as the agent communicates directly with the Zerodha Kite Connect REST API.

---

## 2. Prerequisites

### Zerodha Kite Connect Account
- **Kite Connect Developer Account** with an active API Key and API Secret (available at https://kite.trade).
- **Automated Login Credentials**: Your Kite user ID, password, and the TOTP 2FA secret setup key (revealed when setting up standard TOTP in your Kite security profile).

### Cloud VM
- **OS**: Ubuntu 22.04 LTS (recommended)
- **Minimum specs**: 1 vCPU / 1 GB RAM / 10 GB disk
- **Recommended Region**: **Mumbai, India** (`ap-south-1` on AWS or similar for nearest geographical distance to Zerodha exchanges).

### Software (installed automatically by setup_vm.sh)
- Docker CE 24+
- Docker Compose v2

---

## 3. Step-by-Step VM Setup

### 3a. Provision your VM
Spin up an Ubuntu 22.04 VM in a Mumbai region and SSH in as a non-root user with `sudo` privileges.

### 3b. Run the setup script
Upload `setup_vm.sh` to the VM and execute it:
```bash
# On the VM:
bash setup_vm.sh
```

The script will:
1. Update system packages.
2. Install Docker and Docker Compose.
3. Configure the active user in the `docker` group (remember to log out and log back in to apply group changes).
4. Create the `~/nse-agent/` workspace folder.

---

## 4. Credential Setup

Copy `.env.example` to `.env` inside `~/nse-agent` and configure the following variables:

```bash
cd ~/nse-agent
cp .env.example .env
nano .env
```

| Variable | Description |
|---|---|
| `KITE_API_KEY` | Your Zerodha developer API Key |
| `KITE_API_SECRET` | Your Zerodha developer API Secret |
| `KITE_USER_ID` | Your Zerodha username / client ID |
| `KITE_PASSWORD` | Your Zerodha password |
| `KITE_TOTP_SECRET` | The base32 secret key behind your Kite 2FA TOTP QR code |
| `GMAIL_ADDRESS` | Gmail account used to send EOD reports |
| `GMAIL_APP_PASSWORD` | App-specific password generated in Google Account settings |
| `REPORT_RECIPIENT` | Target email address to receive reports |
| `TRADING_MODE` | `paper` (dry run / mock execution) or `live` |

---

## 5. Automated CI/CD Deployment using GitHub Actions

A GitHub Actions workflow is pre-configured under `.github/workflows/deploy.yml` to trigger deployments automatically on every commit pushed to the `main` branch.

### 5a. Set up GitHub Secrets
To allow the GitHub Actions runner to connect to your EC2 instance securely, add the following secrets in your repository settings (**Settings → Secrets and variables → Actions → New repository secret**):

*   `EC2_HOST`: The public IP address of your EC2 instance.
*   `EC2_USERNAME`: The SSH user (e.g. `ubuntu`).
*   `EC2_SSH_KEY`: The entire contents of your private SSH key file (`.pem`).

### 5b. How the Workflow Operates
1.  **Checkout**: Pulls the codebase on push.
2.  **Authentication**: Loads the SSH private key safely on the runner.
3.  **Sync**: Uses `rsync` to sync code files to `~/nse-agent/` (excluding local `.venv`, local `.env`, database cache, and logs).
4.  **Launch**: Remotely runs a command on EC2 to pre-create folders and launch the container stack using `docker-compose up -d --build`.

---

## 6. Monitoring & Logs

### Stream Container logs
```bash
# Tail log stream
docker-compose logs -f trading-agent

# View last 100 lines
docker-compose logs --tail=100 trading-agent
```

### Check local agent.log
```bash
tail -f ~/nse-agent/logs/agent.log
```

### Trade records CSV
All executed trades are logged directly in:
```bash
cat ~/nse-agent/data/trades.csv
```
