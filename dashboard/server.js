/**
 * Trading Agent — Dashboard Server
 *
 * Express REST API that the dashboard frontend consumes.
 * Reads trades.csv and agent.log from shared Docker volumes,
 * and proxies portfolio/position data from the IBKR CP Gateway.
 *
 * See dashboard/README.md for environment variables and API reference.
 */

"use strict";

const express = require("express");
const fs = require("fs");
const path = require("path");
const https = require("https");
const { parse } = require("csv-parse/sync");
const { execSync } = require("child_process");
const http = require("http");
const Database = require("better-sqlite3");

const app = express();

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const TRADES_CSV = process.env.TRADES_CSV_PATH || "/data/trades.csv";
const AGENT_LOG = process.env.AGENT_LOG_PATH || "/logs/agent.log";
const IBKR_GATEWAY = process.env.IBKR_GATEWAY_URL || "https://localhost:5000";
const MARKET_TYPE = (process.env.MARKET_TYPE || "IN").toUpperCase();
const PORT = parseInt(process.env.PORT || "3000", 10);
const DB_PATH = process.env.TRADING_DB_PATH || "/data/trading.db";

// ---------------------------------------------------------------------------
// SQLite Database
// ---------------------------------------------------------------------------

let _db = null;

function getDB() {
  if (_db) return _db;
  try {
    if (!fs.existsSync(DB_PATH)) return null;
    _db = new Database(DB_PATH, { readonly: true, fileMustExist: true });
    _db.pragma("journal_mode = WAL");
    return _db;
  } catch (err) {
    console.error("Failed to open SQLite DB:", err.message);
    return null;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** HTTPS agent that ignores the IBKR gateway's self-signed certificate. */
const httpsAgent = new https.Agent({ rejectUnauthorized: false });

let ibkrCookie = "";

/** Fetch JSON from the IBKR CP Gateway. Returns null on any error. */
function ibkrGet(apiPath) {
  if (MARKET_TYPE === "US") {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    const url = `${IBKR_GATEWAY}/v1/api${apiPath}`;
    const options = { 
      agent: httpsAgent, 
      family: 4,
      headers: { 
        "Host": "localhost:5000",
        "User-Agent": "Node.js/Dashboard" 
      } 
    };
    if (ibkrCookie) options.headers["Cookie"] = ibkrCookie;

    const req = https.get(url, options, (res) => {
      if (res.headers["set-cookie"]) {
        ibkrCookie = res.headers["set-cookie"].map(c => c.split(';')[0]).join('; ');
      }
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        try {
          const parsed = JSON.parse(data);
          if (parsed.error || parsed.statusCode === 401) {
            console.error(`ibkrGet ${apiPath} API returned error:`, parsed);
            resolve(null);
          } else {
            resolve(parsed);
          }
        } catch (e) {
          console.error(`ibkrGet ${apiPath} JSON parse error:`, e.message, "Data:", data);
          resolve(null);
        }
      });
    });
    req.on("error", (e) => {
      console.error(`ibkrGet ${apiPath} request error:`, e.message);
      resolve(null);
    });
    req.setTimeout(5000, () => {
      console.error(`ibkrGet ${apiPath} timeout`);
      req.destroy();
      resolve(null);
    });
  });
}

/**
 * Read trades from SQLite (with CSV fallback for legacy data).
 * @param {string} [dateFilter] - Only return rows matching this YYYY-MM-DD date.
 * @param {string} [mode] - Filter by 'paper' or 'live'.
 * @param {string} [symbol] - Filter by symbol.
 * @param {number} [limit] - Max rows (default 200).
 */
function readTrades(dateFilter, mode, symbol, limit = 200) {
  const db = getDB();
  if (db) {
    try {
      const clauses = [];
      const params = [];
      if (dateFilter) { clauses.push("date = ?"); params.push(dateFilter); }
      if (mode) { clauses.push("mode = ?"); params.push(mode); }
      if (symbol) { clauses.push("symbol = ?"); params.push(symbol); }
      const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
      params.push(limit);
      return db.prepare(`SELECT * FROM trades ${where} ORDER BY date DESC, time DESC LIMIT ?`)
        .all(...params);
    } catch (err) {
      console.error("SQLite readTrades error:", err.message);
    }
  }
  // Fallback to CSV
  try {
    if (!fs.existsSync(TRADES_CSV)) return [];
    const content = fs.readFileSync(TRADES_CSV, "utf8");
    const records = parse(content, { columns: true, skip_empty_lines: true, relax_column_count: true });
    let filtered = records;
    if (dateFilter) filtered = filtered.filter(r => r.date === dateFilter);
    if (mode) filtered = filtered.filter(r => r.mode === mode);
    if (symbol) filtered = filtered.filter(r => r.symbol === symbol);
    return filtered.slice(0, limit);
  } catch {
    return [];
  }
}

/**
 * Get trade summary aggregates from SQLite.
 */
function getTradeSummary(symbol, sinceDate, mode) {
  const db = getDB();
  if (!db) return { totalBought: 0, totalSold: 0, totalPnl: 0 };
  try {
    const clauses = ["symbol = ?"];
    const params = [symbol];
    if (sinceDate) { clauses.push("date >= ?"); params.push(sinceDate); }
    if (mode) { clauses.push("mode = ?"); params.push(mode); }
    const where = `WHERE ${clauses.join(" AND ")}`;
    const row = db.prepare(`
      SELECT
        COALESCE(SUM(CASE WHEN action='BUY' THEN notional ELSE 0 END), 0) as totalBought,
        COALESCE(SUM(CASE WHEN action='SELL' THEN notional ELSE 0 END), 0) as totalSold,
        COALESCE(SUM(CASE WHEN action='SELL' THEN pnl ELSE 0 END), 0) as totalPnl
      FROM trades ${where}
    `).get(...params);
    return row || { totalBought: 0, totalSold: 0, totalPnl: 0 };
  } catch (err) {
    console.error("getTradeSummary error:", err.message);
    return { totalBought: 0, totalSold: 0, totalPnl: 0 };
  }
}

/**
 * Read signals from SQLite (with JSON file fallback).
 */
function readSignals() {
  const db = getDB();
  if (db) {
    try {
      return db.prepare(`
        SELECT symbol, price, change_pct as changePct, rsi, trend_score as trendScore,
               macd_signal as macdSignal, ema_signal as emaSignal,
               combined_score as combinedScore, signal, confidence,
               buy_threshold as buyThreshold, sell_threshold as sellThreshold,
               ai_decision as aiDecision, ai_reason as aiReason, updated_at
        FROM signals ORDER BY ABS(trend_score) DESC LIMIT 20
      `).all();
    } catch (err) {
      console.error("readSignals SQLite error:", err.message);
    }
  }
  // Fallback to JSON
  const dataPath = path.join(__dirname, "..", "data", "local_signals.json");
  try {
    if (fs.existsSync(dataPath)) {
      return JSON.parse(fs.readFileSync(dataPath, "utf8"));
    }
  } catch { }
  return [];
}

/**
 * Read ML validation logs from SQLite.
 */
function readMLValidations(limit = 100) {
  const db = getDB();
  if (!db) return [];
  try {
    return db.prepare(`
      SELECT timestamp, symbol, action, approved, reason
      FROM ml_validations ORDER BY id DESC LIMIT ?
    `).all(limit);
  } catch (err) {
    console.error("readMLValidations error:", err.message);
    return [];
  }
}

/** Read the last N lines from the agent log file. */
function readLogLines(n = 20) {
  try {
    if (!fs.existsSync(AGENT_LOG)) {
      return ["Agent log not found — agent may not be running"];
    }
    const content = fs.readFileSync(AGENT_LOG, "utf8");
    const lines = content.split("\n").filter((l) => l.trim());
    return lines.slice(-n);
  } catch {
    return ["Error reading agent log"];
  }
}

/** Today's date as YYYY-MM-DD in the local timezone. */
function today() {
  return new Date().toLocaleDateString("en-CA");
}

/** Returns true if the configured market is currently open. */
function isMarketOpen() {
  const now = new Date();
  
  if (MARKET_TYPE === "US") {
    // US Market: 09:30–16:00 America/New_York, Mon-Fri
    const ny = new Date(
      now.toLocaleString("en-US", { timeZone: "America/New_York" })
    );
    const day = ny.getDay();
    if (day === 0 || day === 6) return false;
    const totalMins = ny.getHours() * 60 + ny.getMinutes();
    return totalMins >= 570 && totalMins < 960; // 09:30–16:00
  } else {
    // IN Market: 09:15–15:30 Asia/Kolkata, Mon-Fri
    const kolkata = new Date(
      now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" })
    );
    const day = kolkata.getDay();
    if (day === 0 || day === 6) return false;
    const totalMins = kolkata.getHours() * 60 + kolkata.getMinutes();
    return totalMins >= 555 && totalMins <= 930; // 09:15–15:30
  }
}

// ---------------------------------------------------------------------------
// Market Configuration Route
// ---------------------------------------------------------------------------

app.get("/api/market-config", (req, res) => {
  res.json({ market: MARKET_TYPE });
});

// ---------------------------------------------------------------------------
// Middleware
// ---------------------------------------------------------------------------

app.use(express.json());
app.use(express.urlencoded({ extended: false }));

// Security: Basic Authentication if environment variables are set
const DASHBOARD_USERNAME = process.env.DASHBOARD_USERNAME;
const DASHBOARD_PASSWORD = process.env.DASHBOARD_PASSWORD;

if (DASHBOARD_USERNAME && DASHBOARD_PASSWORD) {
  app.use((req, res, next) => {
    const b64auth = (req.headers.authorization || '').split(' ')[1] || '';
    const [login, password] = Buffer.from(b64auth, 'base64').toString().split(':');
    if (login && password && login === DASHBOARD_USERNAME && password === DASHBOARD_PASSWORD) {
      return next();
    }
    res.set('WWW-Authenticate', 'Basic realm="Trading Dashboard"');
    res.status(401).send('Authentication required.');
  });
}

// Request logging
app.use((req, _res, next) => {
  console.log(`${new Date().toISOString()} ${req.method} ${req.path}`);
  next();
});

// CORS — allow dashboard frontend served from any origin during development
app.use((_req, res, next) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  next();
});

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

function readLocalPositions() {
  try {
    const dataPath = path.join(__dirname, "..", "data", "local_positions.json");
    if (fs.existsSync(dataPath)) {
      const data = fs.readFileSync(dataPath, "utf8");
      const parsed = JSON.parse(data);
      return Object.entries(parsed).map(([symbol, pos]) => ({
        contractDesc: symbol,
        ticker: symbol,
        position: pos.quantity,
        mktPrice: pos.quantity > 0 ? pos.market_value / pos.quantity : 0,
        avgPrice: pos.avg_cost,
        mktValue: pos.market_value,
        unrealizedPnl: pos.market_value - (pos.quantity * pos.avg_cost),
        conid: pos.conid || 0
      }));
    }
  } catch (err) {
    console.error("Error reading local positions:", err);
  }
  return [];
}

function readLocalSummary() {
  try {
    const dataPath = path.join(__dirname, "..", "data", "local_summary.json");
    if (fs.existsSync(dataPath)) {
      const data = fs.readFileSync(dataPath, "utf8");
      return JSON.parse(data);
    }
  } catch (err) {
    console.error("Error reading local summary:", err);
  }
  return null;
}

/**
 * GET /api/portfolio
 * Portfolio summary: NAV, cash, daily P&L, agent status, win rate.
 */
app.get("/api/portfolio", async (_req, res) => {
  try {
    const accounts = await ibkrGet("/portfolio/accounts");
    const paperEnabled = String(process.env.PAPER_TRADING_ENABLED || "true").toLowerCase() !== "false";
    
    let accountId = null;
    if (Array.isArray(accounts)) {
      console.log("IBKR returned accounts:", JSON.stringify(accounts.map(a => a.id || a.accountId)));
      const targetAcc = accounts.find(acc => {
        const id = acc.id || acc.accountId || "";
        return paperEnabled ? id.startsWith("D") : !id.startsWith("D");
      });
      accountId = targetAcc?.id || targetAcc?.accountId || accounts[0]?.id || accounts[0]?.accountId || null;
      console.log(`Selected accountId: ${accountId} (paperEnabled=${paperEnabled})`);
    }

    let nav = 100000;
    let cash = 100000;
    let dailyPnl = 0;
    let positions = [];

    // Read local summary if available
    const localSummary = readLocalSummary();
    if (localSummary) {
      nav = localSummary.portfolio_value ?? nav;
      cash = localSummary.cash ?? cash;
      dailyPnl = localSummary.daily_pnl ?? dailyPnl;
    }

    if (accountId) {
      const summary = await ibkrGet(`/portfolio/${accountId}/summary`);
      if (summary) {
        nav = summary?.netliquidation?.amount ?? nav;
        cash = summary?.availablefunds?.amount ?? cash;
        dailyPnl = summary?.dailypnl?.amount ?? dailyPnl;
      }
      const posData = await ibkrGet(`/portfolio/${accountId}/positions/0`);
      positions = Array.isArray(posData) && posData.length > 0 ? posData : readLocalPositions();
    } else {
      positions = readLocalPositions();
    }

    // Win rate from today's SELL trades
    const tradingMode = process.env.TRADING_MODE || "paper";
    const trades = readTrades(today(), tradingMode);
    const sells = trades.filter((t) => t.action === "SELL" && t.pnl);
    const winners = sells.filter((t) => parseFloat(t.pnl) > 0);
    const winRate =
      sells.length > 0 ? (winners.length / sells.length) * 100 : 0;

    const authStatus = await ibkrGet("/iserver/auth/status");

    // Dynamic agent status checking from local summary write time
    let agentStatus = (accountId || authStatus?.authenticated) ? "running" : "offline";
    if (agentStatus === "offline") {
      const dataPath = path.join(__dirname, "..", "data", "local_summary.json");
      if (fs.existsSync(dataPath)) {
        const stats = fs.statSync(dataPath);
        const mtime = stats.mtime.getTime();
        const now = Date.now();
        if (now - mtime < 120000) { // 2 minutes
          agentStatus = "running";
        }
      }
    }

    res.json({
      nav: parseFloat(nav),
      cash: parseFloat(cash),
      dailyPnl: parseFloat(dailyPnl),
      dailyPnlPct:
        nav > 0 ? (parseFloat(dailyPnl) / (parseFloat(nav) - parseFloat(dailyPnl))) * 100 : 0,
      openPositions: positions.length,
      maxPositions: 5, // Capped to 5 matching risk controls
      winRate: Math.round(winRate * 10) / 10,
      tradesToday: trades.length,
      agentStatus: agentStatus,
      marketOpen: isMarketOpen(),
      lastUpdated: new Date().toISOString(),
    });
  } catch {
    res.status(500).json({ error: "Failed to fetch portfolio" });
  }
});

/**
 * GET /api/positions
 * Open positions with live P&L, stop-loss, and take-profit levels.
 */
app.get("/api/positions", async (_req, res) => {
  try {
    const accounts = await ibkrGet("/portfolio/accounts");
    const paperEnabled = String(process.env.PAPER_TRADING_ENABLED || "true").toLowerCase() !== "false";
    
    let accountId = null;
    if (Array.isArray(accounts)) {
      const targetAcc = accounts.find(acc => {
        const id = acc.id || acc.accountId || "";
        return paperEnabled ? id.startsWith("D") : !id.startsWith("D");
      });
      accountId = targetAcc?.id || targetAcc?.accountId || accounts[0]?.id || accounts[0]?.accountId || null;
    }
    let positions = [];
    if (accountId) {
      const posData = await ibkrGet(`/portfolio/${accountId}/positions/0`);
      positions = Array.isArray(posData) && posData.length > 0 ? posData : readLocalPositions();
    } else {
      positions = readLocalPositions();
    }

    const result = positions.map((pos) => {
      const qty = pos.position || 0;
      const avgCost = pos.avgCost || pos.avgPrice || 0;
      const mktPrice = pos.mktPrice || avgCost;
      const mktValue = pos.mktValue || qty * mktPrice;
      const pnl = mktValue - qty * avgCost;
      const pnlPct = avgCost > 0 ? (pnl / (qty * avgCost)) * 100 : 0;
      return {
        symbol: pos.ticker || pos.contractDesc || "UNKNOWN",
        quantity: qty,
        entryPrice: avgCost,
        currentPrice: mktPrice,
        marketValue: mktValue,
        pnl: Math.round(pnl * 100) / 100,
        pnlPct: Math.round(pnlPct * 100) / 100,
        stopLoss: Math.round(avgCost * 0.98 * 100) / 100,
        takeProfit: Math.round(avgCost * 1.04 * 100) / 100,
        trailingStop: Math.round(mktPrice * 0.985 * 100) / 100,
      };
    });

    res.json(result);
  } catch {
    res.json([]);
  }
});

/**
 * GET /api/signals
 * Signal scanner: Returns algorithmic signals generated by the python agent.
 */
app.get("/api/signals", async (_req, res) => {
  try {
    const authStatus = await ibkrGet("/iserver/auth/status");
    const isAuth = authStatus?.authenticated === true;

    let signals = readSignals();

    signals = signals.map((s) => ({
      ...s,
      ibkrLive: isAuth,
    })).sort((a, b) => Math.abs(b.trendScore) - Math.abs(a.trendScore));

    res.json(signals);
  } catch {
    res.json([]);
  }
});

/**
 * GET /api/trades
 * Today's trades from trades.csv, most recent first (max 50).
 */
app.get("/api/trades", async (_req, res) => {
  try {
    const tradingMode = process.env.TRADING_MODE || "paper";
    const trades = readTrades(today(), tradingMode);
    res.json(trades.slice(0, 50));
  } catch {
    res.json([]);
  }
});

/**
 * GET /api/trades/all
 * All historical trades, most recent first (max 200).
 */
app.get("/api/trades/all", async (_req, res) => {
  try {
    const tradingMode = process.env.TRADING_MODE || "paper";
    const trades = readTrades(null, tradingMode, null, 200);
    res.json(trades);
  } catch {
    res.json([]);
  }
});

/**
 * GET /api/logs
 * Last 20 lines of the agent log file.
 */
app.get("/api/logs", (_req, res) => {
  try {
    res.json(readLogLines(20));
  } catch {
    res.json(["Error reading logs"]);
  }
});

/**
 * GET /api/ticker
 * Returns live top 20 stocks for the marquee ticker (read from agent background process)
 */
app.get("/api/ticker", async (_req, res) => {
  try {
    const tickerFile = path.join("/data", "ticker.json");
    if (fs.existsSync(tickerFile)) {
      const fileData = fs.readFileSync(tickerFile, "utf8");
      const parsedData = JSON.parse(fileData);
      return res.json({ ticker: parsedData.ticker || [] });
    } else {
      res.json({ ticker: [] });
    }
  } catch (error) {
    console.error("Failed to read local ticker data:", error.message);
    res.json({ ticker: [] });
  }
});

/**
 * GET /api/stock/:symbol
 * Returns 6 months of historical prices and trade summary for the symbol.
 */
app.get("/api/stock/:symbol", async (req, res) => {
  const symbol = req.params.symbol;
  try {
    const suffix = MARKET_TYPE === 'IN' ? '.NS' : MARKET_TYPE === 'UK' ? '.L' : '';
    const response = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${symbol}${suffix}?range=6mo&interval=1d`, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
      }
    });
    
    if (!response.ok) {
      throw new Error(`Yahoo Finance API responded with ${response.status}`);
    }
    
    const yfData = await response.json();
    const result = yfData.chart.result[0];
    const timestamps = result.timestamp || [];
    const closes = result.indicators.quote[0].close || [];
    
    const chartData = timestamps.map((ts, i) => ({
      date: new Date(ts * 1000).toISOString().split('T')[0],
      price: closes[i]
    }));

    // 2. Fetch trade history and aggregates from SQLite
    const sixMonthsAgo = new Date();
    sixMonthsAgo.setMonth(sixMonthsAgo.getMonth() - 6);
    const sinceDate = sixMonthsAgo.toISOString().split('T')[0];
    const tradingMode = process.env.TRADING_MODE || "paper";
    
    const summary = getTradeSummary(symbol, sinceDate, tradingMode);
    const trades = readTrades(null, tradingMode, symbol, 100);
    // Filter to 6 months
    const filteredTrades = trades.filter(t => t.date >= sinceDate);

    res.json({
      chartData,
      summary,
      trades: filteredTrades
    });

  } catch (error) {
    console.error(`Error fetching stock data for ${symbol}:`, error.message);
    res.status(500).json({ error: error.message });
  }
});

/**
 * GET /api/ai-reasoning
 * Reads ML validation logs from SQLite
 */
app.get("/api/ai-reasoning", (_req, res) => {
  try {
    const logs = readMLValidations(100);
    if (logs.length > 0) {
      return res.json(logs);
    }
    // Fallback to legacy JSON file
    const aiLogPath = process.env.AI_LOG_PATH || path.join(path.dirname(TRADES_CSV), "ai_validation.json");
    if (!fs.existsSync(aiLogPath)) {
      return res.json([]);
    }
    const content = fs.readFileSync(aiLogPath, "utf8");
    res.json(JSON.parse(content).reverse());
  } catch (err) {
    console.error("Error reading AI validation log:", err);
    res.json([]);
  }
});

async function getActiveAccountId() {
  try {
    const accounts = await ibkrGet("/portfolio/accounts");
    const paperEnabled = String(process.env.PAPER_TRADING_ENABLED || "true").toLowerCase() !== "false";
    let accountId = null;
    if (Array.isArray(accounts)) {
      const targetAcc = accounts.find(acc => {
        const id = acc.id || acc.accountId || "";
        return paperEnabled ? id.startsWith("D") : !id.startsWith("D");
      });
      accountId = targetAcc?.id || targetAcc?.accountId || accounts[0]?.id || accounts[0]?.accountId || null;
    }
    return accountId;
  } catch {
    return null;
  }
}

/**
 * GET /api/apps-health
 * Returns health of dashboard, trading-agent, ibeam, auth-portal
 */
app.get("/api/apps-health", async (_req, res) => {
  const getDockerStatus = (containerName) => {
    return new Promise((resolve) => {
      const options = {
        socketPath: '/var/run/docker.sock',
        path: `/containers/${containerName}/json`,
        method: 'GET'
      };
      const req = http.request(options, (res) => {
        let body = '';
        res.on('data', chunk => body += chunk);
        res.on('end', () => {
          if (res.statusCode === 200) {
            try {
              const info = JSON.parse(body);
              resolve(info.State ? info.State.Status : "offline");
            } catch { resolve("offline"); }
          } else {
            resolve("offline");
          }
        });
      });
      req.on('error', () => resolve("offline"));
      req.end();
    });
  };

  const agentContainerName = MARKET_TYPE === "US" ? "us-trading-agent" : "in-trading-agent";
  let agentStatus = await getDockerStatus(agentContainerName);
  
  if (agentStatus === "running" && !isMarketOpen()) {
    agentStatus = "sleeping";
  }

  res.json({
    dashboard: "running", // If this responds, it's running
    trading_agent: agentStatus,
    ibeam: MARKET_TYPE === "US" ? "running" : await getDockerStatus("ibeam")
  });
});

/**
 * GET /api/logs/ibeam
 * Diagnostic endpoint to view why IB Gateway might be offline
 */
app.get("/api/logs/ibeam", (_req, res) => {
  const options = {
    socketPath: '/var/run/docker.sock',
    path: '/containers/ibeam/logs?stdout=true&stderr=true&tail=100',
    method: 'GET'
  };
  
  const req = http.request(options, (dockerRes) => {
    res.setHeader('Content-Type', 'text/plain');
    if (dockerRes.statusCode !== 200) {
      res.status(dockerRes.statusCode).send("Docker API returned status: " + dockerRes.statusCode);
      return;
    }
    // Docker multiplexed streams have an 8-byte header per chunk. We strip it.
    dockerRes.on('data', chunk => {
      let offset = 0;
      while (offset < chunk.length) {
        if (chunk.length - offset < 8) break;
        const type = chunk[offset]; // 1 for stdout, 2 for stderr
        const length = chunk.readUInt32BE(offset + 4);
        if (offset + 8 + length > chunk.length) break;
        res.write(chunk.slice(offset + 8, offset + 8 + length));
        offset += 8 + length;
      }
    });
    dockerRes.on('end', () => res.end());
  });
  
  req.on('error', (err) => {
    res.setHeader('Content-Type', 'text/plain');
    res.status(500).send("Error fetching logs from docker socket:\n" + err.message);
  });
  
  req.end();
});

/**
 * GET /api/health
 * Health check — returns 200 OK when the server is running.
 */
app.get("/api/health", (_req, res) => {
  res.json({ status: "ok", timestamp: new Date().toISOString() });
});

// Serve static frontend files if they exist (for the built React UI)
const STATIC_DIR = path.join(__dirname, "public");
if (fs.existsSync(STATIC_DIR)) {
  app.use(express.static(STATIC_DIR));
  app.use((_req, res) => {
    res.sendFile(path.join(STATIC_DIR, "index.html"));
  });
}

// ---------------------------------------------------------------------------
// Error handler
// ---------------------------------------------------------------------------

app.use((err, _req, res, _next) => {
  console.error("Unhandled error:", err);
  res.status(500).json({ error: "Internal server error" });
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

if (require.main === module) {
  app.listen(PORT, () => {
    console.log(`${MARKET_TYPE} Trading Dashboard listening on port ${PORT}`);
    console.log(`Trades CSV:   ${TRADES_CSV}`);
    console.log(`Agent Log:    ${AGENT_LOG}`);
  });
}
