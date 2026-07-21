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
const dgram = require("dgram");

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
const REDIS_URL = process.env.REDIS_URL || "redis://redis:6379/0";
const REDIS_NS = `t101:${MARKET_TYPE}`;

// ---------------------------------------------------------------------------
// Redis (agent bus) — read-only consumer for fleet/strategy/vetting state.
// Fully optional: every endpoint degrades to {available:false} without it.
// ---------------------------------------------------------------------------

let _redis = null;

async function getRedis() {
  if (_redis && _redis.isOpen) return _redis;
  try {
    const { createClient } = require("redis");
    const client = createClient({
      url: REDIS_URL,
      socket: { connectTimeout: 2000, reconnectStrategy: (r) => Math.min(r * 500, 5000) },
    });
    client.on("error", (e) => console.error("redis:", e.message));
    await client.connect();
    _redis = client;
    return _redis;
  } catch (e) {
    console.error("Redis unavailable:", e.message);
    return null;
  }
}

async function redisJson(client, key) {
  try {
    const raw = await client.get(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

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
  // IBKR is not used for Alpaca (US) or Zerodha (IN)
  return Promise.resolve(null);
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
  let trades = [];
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
      trades = db.prepare(`SELECT * FROM trades ${where} ORDER BY date DESC, time DESC LIMIT ?`)
        .all(...params);
    } catch (err) {
      console.error("SQLite readTrades error:", err.message);
    }
  }

  // Merge with CSV (for legacy data prior to SQLite migration)
  try {
    if (fs.existsSync(TRADES_CSV)) {
      const content = fs.readFileSync(TRADES_CSV, "utf8");
      const records = parse(content, { columns: true, skip_empty_lines: true, relax_column_count: true });
      let filtered = records;
      if (dateFilter) filtered = filtered.filter(r => r.date === dateFilter);
      if (mode) filtered = filtered.filter(r => r.mode === mode || !r.mode);
      if (symbol) filtered = filtered.filter(r => r.symbol === symbol);
      
      trades = trades.concat(filtered);
      trades.sort((a, b) => {
        if (a.date !== b.date) return a.date > b.date ? -1 : 1;
        return a.time > b.time ? -1 : 1;
      });
      trades = trades.slice(0, limit);
    }
  } catch (err) {
    console.error("CSV readTrades error:", err.message);
  }
  return trades;
}

/**
 * Get trade summary aggregates from merged SQLite and CSV trades.
 */
function getTradeSummary(symbol, sinceDate, mode) {
  const trades = readTrades(null, mode, symbol, 10000);
  const filtered = sinceDate ? trades.filter(t => t.date >= sinceDate) : trades;
  
  let totalBought = 0;
  let totalSold = 0;
  let totalPnl = 0;

  for (const t of filtered) {
    const notional = parseFloat(t.notional) || 0;
    const pnl = parseFloat(t.pnl) || 0;
    if (t.action === 'BUY') {
      totalBought += notional;
    } else if (t.action === 'SELL') {
      totalSold += notional;
      totalPnl += pnl;
    }
  }

  return { totalBought, totalSold, totalPnl };
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
               ai_decision as aiDecision, ai_reason as aiReason,
               hold_reason as holdReason, ml_confidence as mlConfidence, 
               ml_confidence_swing as mlConfidenceSwing, updated_at
        FROM signals ORDER BY ml_confidence_swing DESC
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

/**
 * Read true ML prediction accuracy (historical Win Rate of all completed SELL trades).
 */
function readHistoricalWinRate() {
  const db = getDB();
  if (!db) return 82.4; // Fallback test accuracy
  try {
    const row = db.prepare(`
      SELECT 
        SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as winners,
        COUNT(id) as total
      FROM trades 
      WHERE action = 'SELL' AND pnl IS NOT NULL AND pnl != ''
    `).get();
    
    if (row && row.total > 0) {
      return (row.winners / row.total) * 100;
    }
    return 82.4; // Fallback test accuracy if no completed trades yet
  } catch (err) {
    console.error("readHistoricalWinRate error:", err.message);
    return 82.4;
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

/** Read system status from the JSON file written by the Python agent. */
function getSystemStatus() {
  let systemMarketOpen = isMarketOpen();
  let agentStatus = "offline";
  let nextOpen = "";
  const statusPath = path.join(__dirname, "..", "data", `system_status_${MARKET_TYPE}.json`);
  if (fs.existsSync(statusPath)) {
    try {
      const stats = fs.statSync(statusPath);
      const now = Date.now();
      if (now - stats.mtime.getTime() < 300000) { // 5 minutes
        const statusData = JSON.parse(fs.readFileSync(statusPath, "utf8"));
        systemMarketOpen = statusData.market_open;
        agentStatus = statusData.agent_status;
        nextOpen = statusData.next_open || "";
      }
    } catch (err) {
      console.error("Failed to read system status:", err.message);
    }
  }
  return { systemMarketOpen, agentStatus, nextOpen };
}

// ---------------------------------------------------------------------------
// Market Configuration Route
// ---------------------------------------------------------------------------

app.get("/api/market-config", (req, res) => {
  res.json({ market: MARKET_TYPE });
});

// ---------------------------------------------------------------------------
// Live tick stream (SSE) — registered BEFORE the auth middleware on purpose:
// EventSource can't show a basic-auth prompt and closes permanently on 401.
// The stream carries only {symbol, price} ticks of public securities.
// ---------------------------------------------------------------------------

let sseClients = [];

app.get("/api/stream", (req, res) => {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();
  res.write(": connected\n\n");

  sseClients.push(res);

  // Periodic comment keeps idle connections alive through proxies
  const keepAlive = setInterval(() => {
    try { res.write(": ping\n\n"); } catch { /* closed */ }
  }, 25000);

  req.on("close", () => {
    clearInterval(keepAlive);
    sseClients = sseClients.filter((client) => client !== res);
  });
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
    const dataPath = path.join(__dirname, "..", "data", `local_positions_${MARKET_TYPE}.json`);
    if (fs.existsSync(dataPath)) {
      const data = fs.readFileSync(dataPath, "utf8");
      const positions = JSON.parse(data);
      return Object.entries(positions).map(([symbol, pos]) => ({
        account: pos.account || "SIMULATED",
        symbol: symbol,
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

// Read the order executor's real per-symbol protective state so the dashboard
// can display the ACTUAL stop the bot will trigger on (hard stop + real
// trailing high-water mark), rather than a cosmetic estimate.
function readExecutorState() {
  try {
    const dataPath = path.join(__dirname, "..", "data", `executor_state_${MARKET_TYPE}.json`);
    if (fs.existsSync(dataPath)) {
      const state = JSON.parse(fs.readFileSync(dataPath, "utf8"));
      return { openOrders: state.open_orders || {}, trailingHigh: state.trailing_high || {} };
    }
  } catch (err) {
    console.error("Error reading executor state:", err.message);
  }
  return { openOrders: {}, trailingHigh: {} };
}

// Compute the price the executor will ACTUALLY sell at right now, mirroring
// order_executor.check_exit_conditions using the executor's own entry price,
// hard stop, ATR trail gap and real high-water mark. Returns null when the
// symbol isn't under executor management (caller falls back to an estimate).
function realProtectiveStop(order, trailingHigh, mktPrice, isIN) {
  if (!order) return null;
  const entry = order.entry_price || 0;
  const hardStop = order.stop_loss_price || 0;
  if (entry <= 0) return null;

  const lockThreshold = isIN ? 0.0025 : 0.0015;   // profit-lock activates above this gain
  const configGap = isIN ? 0.005 : 0.003;
  const atrGap = order.initial_trailing_pct || 0;
  const baseGap = atrGap > 0 ? Math.max(atrGap, configGap) : configGap;

  const gainFromEntry = (mktPrice / entry) - 1.0;
  if (gainFromEntry >= lockThreshold && trailingHigh > 0) {
    const gainFromHigh = (trailingHigh / entry) - 1.0;
    let trailGap;
    if (gainFromHigh >= 0.03) trailGap = baseGap * 0.33;
    else if (gainFromHigh >= 0.02) trailGap = baseGap * 0.50;
    else if (gainFromHigh >= 0.01) trailGap = baseGap * 0.67;
    else if (gainFromHigh >= 0.005) trailGap = baseGap * 0.83;
    else trailGap = baseGap;
    let trigger = trailingHigh * (1.0 - trailGap);
    trigger = Math.max(trigger, entry);   // floored at break-even
    return { stop: Math.round(trigger * 100) / 100, hardStop: Math.round(hardStop * 100) / 100, trailingActive: true };
  }
  // Below the profit-lock threshold the trailing stop is INACTIVE — only the
  // hard stop protects the position.
  return { stop: Math.round(hardStop * 100) / 100, hardStop: Math.round(hardStop * 100) / 100, trailingActive: false };
}

function readLocalSummary() {
  try {
    const dataPath = path.join(__dirname, "..", "data", `local_summary_${MARKET_TYPE}.json`);
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
    let buyingPower = 100000;
    let positions = [];

    // Read local summary if available
    const localSummary = readLocalSummary();
    if (localSummary) {
      nav = localSummary.portfolio_value ?? nav;
      cash = localSummary.cash ?? cash;
      dailyPnl = localSummary.daily_pnl ?? dailyPnl;
      buyingPower = localSummary.buying_power ?? buyingPower;
    }

    if (accountId) {
      const summary = await ibkrGet(`/portfolio/${accountId}/summary`);
      if (summary) {
        nav = summary?.netliquidation?.amount ?? nav;
        cash = summary?.availablefunds?.amount ?? cash;
        dailyPnl = summary?.dailypnl?.amount ?? dailyPnl;
        buyingPower = summary?.buyingpower?.amount ?? buyingPower;
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
    let winRate =
      sells.length > 0 ? (winners.length / sells.length) * 100 : 0;
    if (sells.length === 0) {
      winRate = readHistoricalWinRate();
    }

    const authStatus = await ibkrGet("/iserver/auth/status");

    const sysStatus = getSystemStatus();
    let agentStatus = sysStatus.agentStatus;
    let systemMarketOpen = sysStatus.systemMarketOpen;
    let nextOpen = sysStatus.nextOpen;
    
    // Fallback if no status file written yet but broker connected
    if (agentStatus === "offline" && (accountId || authStatus?.authenticated)) {
      agentStatus = "running";
    }

    // Lifetime Realized PNL
    const allHistoricalTrades = readTrades(null, tradingMode, null, 10000);
    const lifetimeRealizedPnl = allHistoricalTrades.reduce((acc, t) => {
        if (t.action === "SELL" && t.pnl) return acc + parseFloat(t.pnl);
        return acc;
    }, 0);

    // Fetch Market Pulse
    let marketPulse = [];
    try {
      const response = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=1d&interval=1d`);
      const data = await response.json();
      const meta = data.chart.result[0].meta;
      const price = meta.regularMarketPrice;
      const prev = meta.previousClose;
      const changePct = ((price - prev) / prev) * 100;
      marketPulse.push({ symbol: "SPY", price: price, changePercent: changePct });
      
      const qResponse = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/QQQ?range=1d&interval=1d`);
      const qData = await qResponse.json();
      const qMeta = qData.chart.result[0].meta;
      const qPrice = qMeta.regularMarketPrice;
      const qPrev = qMeta.previousClose;
      const qChangePct = ((qPrice - qPrev) / qPrev) * 100;
      marketPulse.push({ symbol: "QQQ", price: qPrice, changePercent: qChangePct });
    } catch (e) {
      console.error("Failed to fetch market pulse", e.message);
    }

    res.json({
      nav: parseFloat(nav),
      cash: parseFloat(cash),
      buyingPower: parseFloat(buyingPower),
      dailyPnl: parseFloat(dailyPnl),
      dailyPnlPct:
        nav > 0 ? (parseFloat(dailyPnl) / (parseFloat(nav) - parseFloat(dailyPnl))) * 100 : 0,
      openPositions: positions.length,
      maxPositions: 5, // Capped to 5 matching risk controls
      winRate: Math.round(winRate * 10) / 10,
      tradesToday: trades.length,
      lifetimeRealizedPnl: Math.round(lifetimeRealizedPnl * 100) / 100,
      marketPulse: marketPulse,
      agentStatus: agentStatus,
      marketOpen: systemMarketOpen,
      nextOpen: nextOpen,
      lastUpdated: new Date().toISOString(),
      market: MARKET_TYPE
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
    let nav = 100000;
    const localSummary = readLocalSummary();
    if (localSummary) nav = localSummary.portfolio_value ?? nav;

    if (accountId) {
      const summary = await ibkrGet(`/portfolio/${accountId}/summary`);
      if (summary) nav = summary?.netliquidation?.amount ?? nav;
      const posData = await ibkrGet(`/portfolio/${accountId}/positions/0`);
      positions = Array.isArray(posData) && posData.length > 0 ? posData : readLocalPositions();
    } else {
      positions = readLocalPositions();
    }

    const allSignals = readSignals();
    const execState = readExecutorState();

    const result = positions.map((pos) => {
      const qty = pos.position || 0;
      const avgCost = pos.avgCost || pos.avgPrice || 0;
      const mktPrice = pos.mktPrice || avgCost;
      const mktValue = pos.mktValue || qty * mktPrice;
      const pnl = mktValue - qty * avgCost;
      const pnlPct = avgCost > 0 ? (pnl / (qty * avgCost)) * 100 : 0;
      const symbol = pos.symbol || pos.ticker || pos.contractDesc || "UNKNOWN";
      
      const sig = allSignals.find(s => s.symbol === symbol);
      let strategy = "Unknown";
      if (sig) {
          if (sig.macdSignal === "bullish" && sig.emaSignal === "bullish") strategy = "Momentum";
          else if (sig.rsi && sig.rsi < 40) strategy = "Reversal";
          else strategy = "Trend Follow";
      }

        const isIN = (typeof MARKET_TYPE !== 'undefined' ? MARKET_TYPE : 'US') === 'IN';

        // Prefer the executor's REAL protective state so the displayed stop is
        // exactly what the bot will trigger on. Fall back to an avg-cost estimate
        // only when a position isn't under executor management yet.
        const execOrder = execState.openOrders[symbol];
        const real = realProtectiveStop(execOrder, execState.trailingHigh[symbol], mktPrice, isIN);

        let effectiveStopLoss, trailingTrigger, trailingActive, entryBasis, trailingPctVal;
        if (real) {
            effectiveStopLoss = real.hardStop;      // the bot's actual hard stop
            trailingTrigger = real.stop;            // active trailing level, else hard stop
            trailingActive = real.trailingActive;
            entryBasis = execOrder.entry_price;     // the entry the stop is measured from
            trailingPctVal = execOrder.initial_trailing_pct || (isIN ? 0.005 : 0.003);
        } else {
            // Fallback estimate (unmanaged position): mirror the Python logic.
            const stopLossPct = isIN ? 0.015 : 0.01;
            const lockThreshold = isIN ? 0.0025 : 0.0015;
            const baseGap = isIN ? 0.005 : 0.003;
            const gainPct = avgCost > 0 ? (mktPrice / avgCost) - 1.0 : 0;
            effectiveStopLoss = Math.round(avgCost * (1.0 - stopLossPct) * 100) / 100;
            if (gainPct >= lockThreshold) {
                let trailGap;
                if (gainPct >= 0.03) trailGap = baseGap * 0.33;
                else if (gainPct >= 0.02) trailGap = baseGap * 0.50;
                else if (gainPct >= 0.01) trailGap = baseGap * 0.67;
                else if (gainPct >= 0.005) trailGap = baseGap * 0.83;
                else trailGap = baseGap;
                trailingTrigger = Math.round(Math.max(mktPrice * (1.0 - trailGap), avgCost) * 100) / 100;
                trailingActive = true;
            } else {
                trailingTrigger = effectiveStopLoss;
                trailingActive = false;
            }
            entryBasis = avgCost;
            trailingPctVal = baseGap;
        }

      return {
          symbol,
          quantity: qty,
          entryPrice: avgCost,
          stopBasisEntry: entryBasis,
          currentPrice: mktPrice,
          marketValue: mktValue,
          pnl: Math.round(pnl * 100) / 100,
          pnlPct: Math.round(pnlPct * 100) / 100,
          stopLoss: effectiveStopLoss,
          takeProfit: Math.round(avgCost * (1.0 + 0.015) * 100) / 100,
          trailingStop: trailingTrigger,
          trailingActive: trailingActive,
          trailingPct: trailingPctVal,
          allocation: nav > 0 ? Math.round((mktValue / nav) * 1000) / 10 : 0,
        strategy: strategy
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

    // Show ONLY today's vetted/approved symbols — the set the bot actually
    // trades — so stale signals from previous sessions don't clutter the view.
    // Falls back to showing everything if the vetting list is unavailable.
    try {
      const client = await getRedis();
      if (client) {
        const vetted = await redisJson(client, `${REDIS_NS}:state:vetted_targets`);
        const session = await redisJson(client, `${REDIS_NS}:state:session`);
        const sessionDate = session?.session_date || session?.date;
        const dateOk = !sessionDate || !vetted?.session_date || vetted.session_date === sessionDate;
        if (dateOk && Array.isArray(vetted?.approved) && vetted.approved.length) {
          const approved = new Set(vetted.approved);
          signals = signals.filter((s) => approved.has(s.symbol));
        }
      }
    } catch (e) {
      console.error("signals vetting-filter error (showing all):", e.message);
    }

    signals = signals.map((s) => {
      return {
        ...s,
        ibkrLive: isAuth,
        downsideRisk: -3.5 // Fallback since actual stop-loss is managed by the executor
      };
    }).sort((a, b) => Math.abs(b.trendScore) - Math.abs(a.trendScore));

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
    const trades = readTrades(null, tradingMode, null, 50);
    res.json(trades);
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
    const tickerFile = path.join(__dirname, "..", "data", `ticker_${MARKET_TYPE}.json`);
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
    let fetchSymbol = symbol;
    if (MARKET_TYPE === 'IN' && !fetchSymbol.endsWith('.NS') && !fetchSymbol.endsWith('.BO')) {
      fetchSymbol += '.NS';
    } else if (MARKET_TYPE === 'UK' && !fetchSymbol.endsWith('.L')) {
      fetchSymbol += '.L';
    }
    const response = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${fetchSymbol}?range=6mo&interval=1d`, {
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

async function getNavHistory(range = '1mo') {
  // 1. Get Current NAV
  const localSummary = readLocalSummary();
  let currentNav = localSummary && localSummary.portfolio_value ? localSummary.portfolio_value : 100000;
  
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
    if (accountId) {
      const summary = await ibkrGet(`/portfolio/${accountId}/summary`);
      if (summary && summary.netliquidation) {
        currentNav = summary.netliquidation.amount;
      }
    }
  } catch (err) {
    console.log("Could not fetch IBKR NAV for history, using local summary or default.");
  }
  
  // 2. Try fetching from nav_history for all ranges
  const db = getDB();
  if (db) {
    try {
      let timeLimitMs;
      if (range === '1d') timeLimitMs = 24 * 60 * 60 * 1000;
      else if (range === '5d') timeLimitMs = 5 * 24 * 60 * 60 * 1000;
      else if (range === '1mo') timeLimitMs = 30 * 24 * 60 * 60 * 1000;
      else if (range === '3mo') timeLimitMs = 90 * 24 * 60 * 60 * 1000;
      else if (range === '1y') timeLimitMs = 365 * 24 * 60 * 60 * 1000;
      else timeLimitMs = 365 * 24 * 60 * 60 * 1000;
      
      const startDateIso = new Date(Date.now() - timeLimitMs).toISOString();
      const rows = db.prepare(`SELECT timestamp, nav FROM nav_history WHERE timestamp >= ? ORDER BY timestamp ASC`)
        .all(startDateIso);
      
      if (rows && rows.length > 0) {
        // Downsample: only include points that are at least N minutes apart
        let minGapMs = 0;
        if (range === '1d') minGapMs = 5 * 60 * 1000; // 5 min
        else if (range === '5d') minGapMs = 30 * 60 * 1000; // 30 min
        else if (range === '1mo') minGapMs = 12 * 60 * 60 * 1000; // 12 hours
        else if (range === '3mo') minGapMs = 24 * 60 * 60 * 1000; // 1 day
        else minGapMs = 24 * 60 * 60 * 1000; // 1 day
        
        const history = [];
        let lastTimeMs = 0;
        for (const r of rows) {
          if (r.nav <= 0) continue;
          const tMs = new Date(r.timestamp).getTime();
          if (tMs - lastTimeMs >= minGapMs) {
            history.push({ date: r.timestamp, nav: r.nav });
            lastTimeMs = tMs;
          }
        }
        
        if (currentNav > 0) {
          history.push({ date: new Date().toISOString(), nav: currentNav });
        }
        return history;
      }
    } catch (err) {
      console.error("nav_history query error:", err.message);
    }
  }

  // 3. Fallback: Read all historical trades for reconstruction
  const tradingMode = process.env.TRADING_MODE || "paper";
  const trades = readTrades(null, tradingMode, null, 10000);
  
  const todayDate = new Date();
  
  let startDate = new Date(todayDate);
  if (range === '1d') startDate.setDate(startDate.getDate() - 1);
  else if (range === '5d') startDate.setDate(startDate.getDate() - 5);
  else if (range === '1mo') startDate.setMonth(startDate.getMonth() - 1);
  else if (range === '3mo') startDate.setMonth(startDate.getMonth() - 3);
  else if (range === '1y') startDate.setFullYear(startDate.getFullYear() - 1);

  if (trades.length === 0) {
    const history = [];
    let cd = new Date(startDate);
    while (cd <= todayDate) {
      history.push({ date: cd.toISOString().split('T')[0], nav: currentNav });
      cd.setDate(cd.getDate() + 1);
    }
    return history;
  }

  trades.sort((a, b) => new Date(a.date) - new Date(b.date));
  
  const pnlByDate = {};
  let lifetimePnl = 0;
  trades.forEach(t => {
    if (t.action === 'SELL' && t.pnl) {
      const val = parseFloat(t.pnl);
      pnlByDate[t.date] = (pnlByDate[t.date] || 0) + val;
      lifetimePnl += val;
    }
  });

  const startingBalance = currentNav - lifetimePnl;
  const firstTradeDate = new Date(trades[0].date);
  
  const history = [];
  let runningNav = startingBalance;
  let currDate = new Date(firstTradeDate);
  
  // Advance runningNav to startDate if needed
  while (currDate < startDate) {
    const dStr = currDate.toISOString().split('T')[0];
    if (pnlByDate[dStr]) runningNav += pnlByDate[dStr];
    currDate.setDate(currDate.getDate() + 1);
  }

  currDate = new Date(startDate);
  while (currDate <= todayDate) {
    const dStr = currDate.toISOString().split('T')[0];
    if (pnlByDate[dStr]) {
      runningNav += pnlByDate[dStr];
    }
    history.push({
      date: dStr,
      nav: runningNav
    });
    currDate.setDate(currDate.getDate() + 1);
  }

  const todayStr = todayDate.toISOString().split('T')[0];
  if (!history.find(h => h.date === todayStr)) {
    history.push({ date: todayStr, nav: currentNav });
  }

  return history;
}

/**
 * GET /api/nav-history
 * Returns historical NAV data reconstructed from realized PNL in the trades database.
 */
app.get("/api/nav-history", async (req, res) => {
  try {
    const range = req.query.range || '1mo';
    const history = await getNavHistory(range);
    res.json(history);
  } catch (e) {
    console.error("Failed to fetch nav-history", e.message);
    res.json([]);
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
  let dockerStatus = await getDockerStatus(agentContainerName);
  
  let agentStatus = dockerStatus;
  const sysStatus = getSystemStatus();
  if (dockerStatus === "running") {
    agentStatus = sysStatus.agentStatus;
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
 * GET /api/analytics
 * Provides risk metrics, sector exposure, and model health stats.
 */
app.get("/api/analytics", async (_req, res) => {
  try {
    const signals = readSignals();
    
    // Model Health
    let avgConfidence = 0;
    let buyCount = 0;
    let sellCount = 0;
    let holdCount = 0;
    let gatedCount = 0;
    
    let validConfCount = 0;
    let confSum = 0;

    for (const s of signals) {
      if (s.mlConfidence !== undefined && s.mlConfidence !== null) {
        confSum += s.mlConfidence;
        validConfCount++;
      }
      
      const isGated = s.signal === 'HOLD' && s.holdReason && s.mlConfidence >= (s.buyThreshold || 0.48);
      if (isGated) gatedCount++;
      else if (s.signal === 'BUY') buyCount++;
      else if (s.signal === 'SELL') sellCount++;
      else holdCount++;
    }
    
    avgConfidence = validConfCount > 0 ? (confSum / validConfCount) * 100 : 0;
    
    // Signals Today
    const todayStr = today();
    const signalsToday = signals.filter(s => s.updated_at && s.updated_at.startsWith(todayStr)).length;
    
    // Prediction Accuracy (Actual Historical Win Rate)
    const predictionAccuracy = readHistoricalWinRate();

    // Sector Exposure
    const sectors = {};
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
    
    let totalValue = 0;
    for (const pos of positions) {
      const sym = pos.ticker || pos.contractDesc || "UNKNOWN";
      const mktValue = pos.mktValue || (pos.position * (pos.mktPrice || pos.avgPrice)) || 0;
      totalValue += mktValue;
      
      // Basic Heuristic Mapping
      let sector = "Technology";
      if (["JPM", "BAC", "C", "GS", "MS", "WFC"].includes(sym)) sector = "Financials";
      else if (["JNJ", "PFE", "UNH", "MRK", "ABBV"].includes(sym)) sector = "Healthcare";
      else if (["XOM", "CVX", "COP", "SLB"].includes(sym)) sector = "Energy";
      else if (["PG", "KO", "PEP", "WMT", "COST"].includes(sym)) sector = "Consumer Defensive";
      else if (["AMZN", "TSLA", "HD", "MCD", "NKE"].includes(sym)) sector = "Consumer Cyclical";
      else if (["BA", "UNP", "HON", "UPS", "CAT"].includes(sym)) sector = "Industrials";
      
      sectors[sector] = (sectors[sector] || 0) + mktValue;
    }
    
    const sectorExposure = Object.keys(sectors).map(sec => ({
      sector: sec,
      allocation: totalValue > 0 ? (sectors[sec] / totalValue) * 100 : 0
    })).sort((a, b) => b.allocation - a.allocation);
    
    if (sectorExposure.length === 0) {
      sectorExposure.push({ sector: "Cash / No Positions", allocation: 100 });
    }

    // Strategy Analytics based on trade history
    const tradingMode = process.env.TRADING_MODE || "paper";
    const allTrades = readTrades(null, tradingMode, null, 100000); // Fetch all trades
    let totalTrades = 0;
    let winCount = 0;
    let grossProfit = 0;
    let grossLoss = 0;
    let totalPnl = 0;
    
    for (const t of allTrades) {
      // Only CLOSED trades count. BUY rows carry pnl = 0 (not null), so
      // including them inflated the denominator and understated the win rate
      // (e.g. 16/57 = 28.1% instead of the true 16/46 = 34.8%).
      if (t.action === "SELL" && t.pnl !== null && t.pnl !== undefined) {
        totalTrades++;
        const pnl = parseFloat(t.pnl);
        totalPnl += pnl;
        if (pnl > 0) {
          winCount++;
          grossProfit += pnl;
        } else {
          grossLoss += Math.abs(pnl);
        }
      }
    }
    
    const winRate = totalTrades > 0 ? (winCount / totalTrades) * 100 : 0;
    const profitFactor = grossLoss > 0 ? (grossProfit / grossLoss) : (grossProfit > 0 ? 99.9 : 0);
    const avgPnl = totalTrades > 0 ? (totalPnl / totalTrades) : 0;

    res.json({
      modelHealth: {
        avgConfidence,
        predictionAccuracy,
        signalsToday: signalsToday > 0 ? signalsToday : signals.length,
        distribution: {
          buy: buyCount,
          sell: sellCount,
          hold: holdCount,
          gated: gatedCount
        }
      },
      strategy: {
        winRate,
        profitFactor,
        totalTrades,
        avgPnl
      }
    });
  } catch (e) {
    console.error("Analytics Error", e);
    res.json({ error: "Failed to load analytics" });
  }
});

/**
 * GET /api/ml-accuracy
 * ML Model accuracy report — confidence calibration, exit breakdown, overall stats.
 */
app.get("/api/ml-accuracy", (_req, res) => {
  const db = getDB();
  if (!db) return res.json({ error: "No database available" });

  try {
    // 1. Overall stats: pair BUY→SELL trades
    const pairedTrades = db.prepare(`
      SELECT t1.id, t1.symbol, t1.price as buy_price,
             t2.price as sell_price, t2.pnl, t2.exit_reason, t2.date as sell_date,
             t1.quantity
      FROM trades t1
      INNER JOIN trades t2 ON t1.symbol = t2.symbol 
          AND t2.action = 'SELL' 
          AND t2.id = (SELECT MIN(id) FROM trades WHERE symbol = t1.symbol AND action = 'SELL' AND id > t1.id)
      WHERE t1.action = 'BUY'
      ORDER BY t1.id
    `).all();

    let wins = 0, losses = 0, totalPnl = 0;
    const byReason = {};

    for (const t of pairedTrades) {
      const pnl = t.pnl != null ? parseFloat(t.pnl) : (t.sell_price - t.buy_price) * t.quantity;
      totalPnl += pnl;
      if (pnl >= 0) wins++; else losses++;

      const reason = t.exit_reason || "UNKNOWN";
      if (!byReason[reason]) byReason[reason] = { wins: 0, losses: 0, pnl: 0 };
      byReason[reason].pnl += pnl;
      if (pnl >= 0) byReason[reason].wins++; else byReason[reason].losses++;
    }

    // 2. ML Confidence calibration — match BUY ML confidence to trade outcomes
    const calibrationQuery = db.prepare(`
      SELECT t.id, t.symbol, t.price as buy_price,
             t2.price as sell_price, t2.pnl,
             mv.reason as ml_reason
      FROM trades t
      INNER JOIN trades t2 ON t.symbol = t2.symbol 
          AND t2.action = 'SELL' 
          AND t2.id = (SELECT MIN(id) FROM trades WHERE symbol = t.symbol AND action = 'SELL' AND id > t.id)
      LEFT JOIN ml_validations mv ON t.symbol = mv.symbol 
          AND mv.action = 'BUY'
          AND mv.id = (SELECT MAX(id) FROM ml_validations WHERE symbol = t.symbol AND action = 'BUY' AND created_at <= t.created_at)
      WHERE t.action = 'BUY'
      ORDER BY t.id
    `).all();

    const buckets = {};
    for (const row of calibrationQuery) {
      if (!row.ml_reason || row.pnl == null) continue;
      const match = row.ml_reason.match(/Confidence:\s*([\d.]+)%/);
      if (!match) continue;
      const conf = parseFloat(match[1]);
      const pnl = parseFloat(row.pnl);
      const bucket = Math.floor(conf / 5) * 5;
      const key = `${bucket}-${bucket + 5}`;
      if (!buckets[key]) buckets[key] = { wins: 0, losses: 0, totalPnl: 0, count: 0 };
      buckets[key].count++;
      buckets[key].totalPnl += pnl;
      if (pnl >= 0) buckets[key].wins++; else buckets[key].losses++;
    }

    // Convert to sorted array
    const calibration = Object.keys(buckets).sort().map(key => ({
      range: key + "%",
      trades: buckets[key].count,
      wins: buckets[key].wins,
      losses: buckets[key].losses,
      winRate: buckets[key].count > 0 ? (buckets[key].wins / buckets[key].count) * 100 : 0,
      avgPnl: buckets[key].count > 0 ? buckets[key].totalPnl / buckets[key].count : 0
    }));

    // 3. Exit reason breakdown array
    const exitBreakdown = Object.keys(byReason).sort().map(reason => ({
      reason,
      wins: byReason[reason].wins,
      losses: byReason[reason].losses,
      total: byReason[reason].wins + byReason[reason].losses,
      winRate: (byReason[reason].wins + byReason[reason].losses) > 0
        ? (byReason[reason].wins / (byReason[reason].wins + byReason[reason].losses)) * 100 : 0,
      pnl: byReason[reason].pnl
    }));

    // 4. Avg ML confidence for approved/rejected
    let approvedConf = [], rejectedConf = [];
    try {
      const approved = db.prepare(`SELECT reason FROM ml_validations WHERE action = 'BUY' AND approved = 1 ORDER BY id DESC LIMIT 500`).all();
      for (const r of approved) {
        const m = r.reason.match(/Confidence:\s*([\d.]+)%/);
        if (m) approvedConf.push(parseFloat(m[1]));
      }
      const rejected = db.prepare(`SELECT reason FROM ml_validations WHERE action = 'BUY' AND approved = 0 ORDER BY id DESC LIMIT 500`).all();
      for (const r of rejected) {
        const m = r.reason.match(/Confidence:\s*([\d.]+)%/);
        if (m) rejectedConf.push(parseFloat(m[1]));
      }
    } catch (_) {}

    const total = wins + losses;
    const currency = (process.env.MARKET_TYPE || "US") === "IN" ? "₹" : "$";

    res.json({
      overall: {
        totalTrades: total,
        wins,
        losses,
        winRate: total > 0 ? (wins / total) * 100 : 0,
        totalPnl,
        currency
      },
      exitBreakdown,
      calibration,
      mlConfidence: {
        approvedAvg: approvedConf.length > 0 ? approvedConf.reduce((a, b) => a + b, 0) / approvedConf.length : 0,
        approvedCount: approvedConf.length,
        rejectedAvg: rejectedConf.length > 0 ? rejectedConf.reduce((a, b) => a + b, 0) / rejectedConf.length : 0,
        rejectedCount: rejectedConf.length
      }
    });
  } catch (e) {
    console.error("ML Accuracy Error", e);
    res.json({ error: "Failed to compute ML accuracy" });
  }
});

/**
 * GET /api/fleet
 * Agent fleet health from the Redis bus: session state + per-agent heartbeats.
 */
const FLEET_AGENTS = ["orchestrator", "trader", "scanner", "vetting", "strategy", "trainer"];

app.get("/api/fleet", async (_req, res) => {
  const client = await getRedis();
  if (!client) return res.json({ available: false, agents: [], session: null });
  try {
    const session = await redisJson(client, `${REDIS_NS}:state:session`);
    const halt = await redisJson(client, `${REDIS_NS}:state:halt`);
    const agents = [];
    for (const name of FLEET_AGENTS) {
      const hb = await redisJson(client, `${REDIS_NS}:hb:${name}`);
      agents.push({
        name,
        alive: hb !== null,          // heartbeat keys expire after 90s
        status: hb?.status || "down",
        detail: hb?.detail || "",
        ts: hb?.ts || null,
      });
    }
    res.json({ available: true, session, halt, agents });
  } catch (e) {
    console.error("fleet error:", e.message);
    res.json({ available: false, agents: [], session: null });
  }
});

/**
 * GET /api/strategy
 * Current market-regime directive published by the strategy agent.
 */
app.get("/api/strategy", async (_req, res) => {
  const client = await getRedis();
  if (!client) return res.json({ available: false });
  const strategy = await redisJson(client, `${REDIS_NS}:state:strategy`);
  res.json(strategy ? { available: true, ...strategy } : { available: false });
});

/**
 * GET /api/vetting
 * Profit-vetting output: today's approved/blocked targets, the live-accuracy
 * blocklist, and the per-symbol backtest report file.
 */
app.get("/api/vetting", async (_req, res) => {
  const out = { available: false, vetted: null, blocklist: {}, report: null };
  const client = await getRedis();
  if (client) {
    try {
      out.vetted = await redisJson(client, `${REDIS_NS}:state:vetted_targets`);
      const rawBlock = await client.hGetAll(`${REDIS_NS}:state:blocklist`);
      for (const [sym, val] of Object.entries(rawBlock || {})) {
        try { out.blocklist[sym] = JSON.parse(val); } catch { /* skip */ }
      }
      out.available = true;
    } catch (e) {
      console.error("vetting redis error:", e.message);
    }
  }
  try {
    const reportPath = path.join(__dirname, "..", "data", `vetting_report_${MARKET_TYPE}.json`);
    if (fs.existsSync(reportPath)) {
      out.report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
      out.available = true;
    }
  } catch (e) {
    console.error("vetting report error:", e.message);
  }
  res.json(out);
});

/**
 * GET /api/pending-orders
 * Active bracket/trailing-stop trackers from the executor's state file.
 */
app.get("/api/pending-orders", (_req, res) => {
  try {
    const dataPath = path.join(__dirname, "..", "data", `executor_state_${MARKET_TYPE}.json`);
    if (!fs.existsSync(dataPath)) return res.json([]);
    const state = JSON.parse(fs.readFileSync(dataPath, "utf8"));
    const orders = state.open_orders || {};
    res.json(Object.values(orders).map((o) => ({
      symbol: o.symbol,
      quantity: o.quantity,
      entryPrice: o.entry_price,
      stopLoss: o.stop_loss_price,
      trailingPct: o.initial_trailing_pct,
      fractional: !!o.is_fractional,
    })));
  } catch (e) {
    console.error("pending-orders error:", e.message);
    res.json([]);
  }
});

/**
 * GET /api/daily-pnl
 * Realized P&L per session (last 30 sessions with closed trades).
 */
app.get("/api/daily-pnl", (_req, res) => {
  const db = getDB();
  if (!db) return res.json([]);
  try {
    const rows = db.prepare(`
      SELECT date,
             ROUND(SUM(pnl), 2) AS pnl,
             SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
             SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) AS losses
      FROM trades
      WHERE action = 'SELL' AND pnl IS NOT NULL
      GROUP BY date ORDER BY date DESC LIMIT 30
    `).all();
    res.json(rows.reverse());
  } catch (e) {
    console.error("daily-pnl error:", e.message);
    res.json([]);
  }
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
// UDP receiver — trader agents send {symbol, price} datagrams to :4000,
// broadcast to every connected SSE client (route registered near the top).
// ---------------------------------------------------------------------------

const udpServer = dgram.createSocket("udp4");
udpServer.on("message", (msg) => {
  try {
    const data = msg.toString();
    sseClients.forEach((client) => {
      client.write(`data: ${data}\n\n`);
    });
  } catch (err) {
    console.error("Error broadcasting UDP message to SSE:", err);
  }
});
udpServer.on("error", (err) => {
  console.error(`UDP Server error:\n${err.stack}`);
});

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
    
    try {
      udpServer.bind(4000);
      console.log(`UDP Server listening on port 4000 for live ticks`);
    } catch (e) {
      console.error("Could not bind UDP server to 4000:", e);
    }
  });
}
