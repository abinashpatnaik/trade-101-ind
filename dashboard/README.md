# Trading Dashboard

A real-time web dashboard for monitoring the FTSE 100 Trading Agent. Displays live portfolio data, open positions, signal scanner, trade log, and agent logs.

## What It Shows

| Panel | Data Source | Description |
|-------|-------------|-------------|
| Portfolio Summary | IBKR CP API | NAV, daily P&L, cash, open positions count, win rate |
| Signal Scanner | IBKR market data | RSI, trend score, MACD signal for all 20 stocks |
| Open Positions | IBKR CP API | Symbol, quantity, entry price, current P&L, stop levels |
| Trade Log | `trades.csv` | All trades placed today with prices and P&L |
| Agent Terminal | `agent.log` | Last 20 lines of the agent's activity log |

The dashboard is **read-only** — it does not place orders or modify agent configuration.

---

## Running the Dashboard

### With Docker Compose (recommended)

The dashboard runs automatically as part of the full stack:

```bash
cd agent/
docker-compose up -d
# Dashboard available at http://<EC2-IP>:3000
```

### Standalone (local development)

```bash
cd dashboard/
npm install
# Set environment variables
export TRADES_CSV_PATH=../data/trades.csv
export AGENT_LOG_PATH=../logs/agent.log
export IBKR_GATEWAY_URL=https://localhost:5000
npm start
# Dashboard at http://localhost:3000
```

### Development mode (with auto-reload)

```bash
cd dashboard/
npm install
npm run dev   # Requires nodemon: npm install -g nodemon
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `3000` | HTTP port the server listens on |
| `TRADES_CSV_PATH` | `/data/trades.csv` | Path to trades CSV file (Docker volume mount) |
| `AGENT_LOG_PATH` | `/logs/agent.log` | Path to agent log file (Docker volume mount) |
| `IBKR_GATEWAY_URL` | `https://localhost:5000` | URL of authenticated IBKR CP Gateway |

---

## API Endpoints

The dashboard exposes a JSON REST API that the frontend consumes. You can also query these directly for debugging or to build custom integrations.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/portfolio` | GET | Portfolio summary: NAV, P&L, cash, agent status |
| `GET /api/positions` | GET | All open positions with live P&L and stop levels |
| `GET /api/signals` | GET | Signal scanner: RSI, trend score, signal for all 20 stocks |
| `GET /api/trades` | GET | Today's trades from `trades.csv` (most recent first) |
| `GET /api/trades/all` | GET | All historical trades (up to 200 most recent) |
| `GET /api/logs` | GET | Last 20 lines of `agent.log` |
| `GET /api/health` | GET | Health check: `{"status": "ok", "timestamp": "..."}` |

### Example API Responses

**`GET /api/portfolio`**
```json
{
  "nav": 102450.50,
  "cash": 67890.20,
  "dailyPnl": 1240.30,
  "dailyPnlPct": 1.22,
  "openPositions": 3,
  "maxPositions": 10,
  "winRate": 62.5,
  "tradesToday": 8,
  "agentStatus": "running",
  "marketOpen": true,
  "lastUpdated": "2026-01-15T10:30:45.000Z"
}
```

**`GET /api/signals`** (excerpt)
```json
[
  {
    "symbol": "HSBA",
    "price": 752.40,
    "changePct": 0.82,
    "rsi": 42.1,
    "trendScore": 0.58,
    "macdSignal": "bullish",
    "emaSignal": "bullish",
    "signal": "BUY",
    "confidence": 58,
    "ibkrLive": true
  }
]
```

**`GET /api/trades`** (excerpt)
```json
[
  {
    "date": "2026-01-15",
    "symbol": "LLOY",
    "action": "SELL",
    "price": "56.80",
    "quantity": "350",
    "value": "19880.00",
    "pnl": "420.00",
    "reason": "take_profit"
  }
]
```

---

## Connecting to a Remote Agent

If the dashboard is running on your local machine but the trading agent is on EC2, set:

```bash
export IBKR_GATEWAY_URL=https://<EC2-IP>:5000
export TRADES_CSV_PATH=/path/to/mounted/trades.csv
```

Alternatively, use SSH port forwarding to tunnel the IBKR gateway:

```bash
ssh -L 5000:localhost:5000 ubuntu@<EC2-IP>
```

Then run the dashboard locally pointing to `https://localhost:5000`.

---

## Architecture Note

The dashboard is a lightweight complement to the trading agent. The full-featured React + TypeScript dashboard (in the `/trading-dashboard` repo directory) provides a richer UI with charts and animations. The `server.js` file in this directory is the same Express backend that powers both the simple and full-featured frontends.
