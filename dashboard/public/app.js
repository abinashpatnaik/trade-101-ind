/* ==========================================================================
   Trading Dashboard — frontend
   Vanilla JS + Chart.js. Poll cadence: 5s core, 30s heavy. Prices stream
   over SSE (/api/stream) and patch the tape + positions in place.
   ========================================================================== */

"use strict";

let MARKET = "US";

/* ------------------------- formatters ---------------------------------- */

const fmtMoney = (v) =>
  new Intl.NumberFormat(MARKET === "IN" ? "en-IN" : "en-US", {
    style: "currency",
    currency: MARKET === "IN" ? "INR" : "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number.isFinite(+v) ? +v : 0);

const fmtSigned = (v) => `${+v >= 0 ? "+" : ""}${fmtMoney(v)}`;
const fmtPct = (v, dp = 1) => `${(+v).toFixed(dp)}%`;
const fmtSignedPct = (v, dp = 2) => `${+v >= 0 ? "+" : ""}${(+v).toFixed(dp)}%`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const $ = (id) => document.getElementById(id);

/* ------------------------- theme --------------------------------------- */

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("dash-theme", theme);
  $("icon-sun").style.display = theme === "dark" ? "" : "none";
  $("icon-moon").style.display = theme === "dark" ? "none" : "";
  restyleCharts();
}

function initTheme() {
  const saved = localStorage.getItem("dash-theme");
  const theme = saved || "dark";
  document.documentElement.setAttribute("data-theme", theme);
  $("icon-sun").style.display = theme === "dark" ? "" : "none";
  $("icon-moon").style.display = theme === "dark" ? "none" : "";
  $("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
  });
}

/* ------------------------- chips ---------------------------------------- */

function setChip(id, text, tone) {
  const el = $(id);
  if (!el) return;
  el.hidden = false;
  el.querySelector("span:last-child").textContent = text;
  el.classList.remove("is-good", "is-warn", "is-bad", "is-info");
  if (tone) el.classList.add(`is-${tone}`);
}

/* ------------------------- charts ---------------------------------------- */

let navChart = null;
let pnlChart = null;
let calibChart = null;
let stockChart = null;
let navRange = "1d";
let lastNavRows = [];
let lastPnlRows = [];
let lastCalib = null;

function chartDefaults() {
  Chart.defaults.color = cssVar("--muted");
  Chart.defaults.borderColor = cssVar("--grid");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 11;
}

function baseScales(isTime) {
  return {
    x: {
      type: isTime ? "time" : "category",
      grid: { display: false },
      border: { color: cssVar("--baseline") },
      ticks: { maxTicksLimit: 7, color: cssVar("--muted") },
    },
    y: {
      grid: { color: cssVar("--grid") },
      border: { display: false },
      ticks: { maxTicksLimit: 6, color: cssVar("--muted") },
    },
  };
}

function tooltipStyle() {
  return {
    backgroundColor: cssVar("--surface-2"),
    titleColor: cssVar("--ink"),
    bodyColor: cssVar("--ink-2"),
    borderColor: cssVar("--baseline"),
    borderWidth: 1,
    padding: 10,
    displayColors: false,
  };
}

function renderNavChart(rows) {
  lastNavRows = rows;
  const ctx = $("nav-chart");
  if (!ctx) return;
  if (navChart) navChart.destroy();
  const s1 = cssVar("--s1");
  navChart = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [{
        label: "NAV",
        data: rows.map((r) => ({ x: r.date || r.timestamp, y: r.nav })),
        borderColor: s1,
        borderWidth: 2,
        pointRadius: 0,
        pointHitRadius: 12,
        tension: 0.25,
        fill: true,
        backgroundColor: (c) => {
          const g = c.chart.ctx.createLinearGradient(0, 0, 0, c.chart.height || 280);
          g.addColorStop(0, s1 + "33");
          g.addColorStop(1, s1 + "00");
          return g;
        },
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { ...tooltipStyle(), callbacks: { label: (i) => ` ${fmtMoney(i.parsed.y)}` } },
      },
      scales: baseScales(true),
    },
  });
  // Accessible table fallback
  const tbl = $("nav-table");
  if (tbl) {
    const step = Math.max(1, Math.floor(rows.length / 12));
    tbl.innerHTML =
      "<thead><tr><th scope='col'>Time</th><th scope='col' class='right'>NAV</th></tr></thead><tbody>" +
      rows.filter((_, i) => i % step === 0)
        .map((r) => `<tr><td>${esc(new Date(r.date || r.timestamp).toLocaleString())}</td><td class="right num">${fmtMoney(r.nav)}</td></tr>`)
        .join("") + "</tbody>";
  }
}

function renderPnlChart(rows) {
  lastPnlRows = rows;
  const ctx = $("pnl-chart");
  if (!ctx) return;
  if (pnlChart) pnlChart.destroy();
  const up = cssVar("--up");
  const down = cssVar("--down");
  pnlChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: rows.map((r) => r.date.slice(5)),
      datasets: [{
        label: "Realized P&L",
        data: rows.map((r) => r.pnl),
        backgroundColor: rows.map((r) => (r.pnl >= 0 ? up : down)),
        borderRadius: 4,
        maxBarThickness: 26,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          ...tooltipStyle(),
          callbacks: {
            label: (i) => {
              const r = rows[i.dataIndex];
              return ` ${fmtSigned(r.pnl)}  ·  ${r.wins}W / ${r.losses}L`;
            },
          },
        },
      },
      scales: {
        ...baseScales(false),
        y: { ...baseScales(false).y, grid: { color: cssVar("--grid") } },
      },
    },
  });
  const tbl = $("pnl-table");
  if (tbl) {
    tbl.innerHTML =
      "<thead><tr><th scope='col'>Date</th><th scope='col' class='right'>P&L</th><th scope='col' class='right'>Wins</th><th scope='col' class='right'>Losses</th></tr></thead><tbody>" +
      rows.map((r) =>
        `<tr><td>${esc(r.date)}</td><td class="right num"><span class="${r.pnl >= 0 ? "up" : "down"}">${fmtSigned(r.pnl)}</span></td><td class="right num">${r.wins}</td><td class="right num">${r.losses}</td></tr>`
      ).join("") + "</tbody>";
  }
}

function renderCalibChart(calib) {
  // calib: [{range: "55-60%", trades, wins, losses, winRate, avgPnl}, …]
  lastCalib = calib;
  const ctx = $("calib-chart");
  if (!ctx || !Array.isArray(calib) || !calib.length) return;
  if (calibChart) calibChart.destroy();
  const buckets = [...calib].sort((a, b) => parseInt(a.range) - parseInt(b.range));
  const actual = buckets.map((b) => b.winRate ?? 0);
  const expected = buckets.map((b) => {
    const [lo, hi] = b.range.replace("%", "").split("-").map(Number);
    return (lo + (hi || lo)) / 2;
  });
  calibChart = new Chart(ctx, {
    data: {
      labels: buckets.map((b) => b.range),
      datasets: [
        {
          type: "bar",
          label: "Actual win rate",
          data: actual,
          backgroundColor: cssVar("--s1"),
          borderRadius: 4,
          maxBarThickness: 30,
        },
        {
          type: "line",
          label: "Perfect calibration",
          data: expected,
          borderColor: cssVar("--muted"),
          borderDash: [5, 4],
          borderWidth: 1.5,
          pointRadius: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          labels: { boxWidth: 10, boxHeight: 10, color: cssVar("--ink-2") },
        },
        tooltip: { ...tooltipStyle(), callbacks: { label: (i) => ` ${i.dataset.label}: ${i.parsed.y.toFixed(0)}%` } },
      },
      scales: {
        ...baseScales(false),
        y: { ...baseScales(false).y, min: 0, max: 100, ticks: { callback: (v) => v + "%" } },
      },
    },
  });
}

function restyleCharts() {
  chartDefaults();
  if (lastNavRows.length) renderNavChart(lastNavRows);
  if (lastPnlRows.length) renderPnlChart(lastPnlRows);
  if (lastCalib) renderCalibChart(lastCalib);
}

/* ------------------------- header / session ------------------------------ */

function renderHeader(portfolio, fleet, strategy) {
  if (portfolio) {
    const agentTone = portfolio.agentStatus === "running" ? "good" : portfolio.agentStatus === "sleeping" ? "warn" : "bad";
    setChip("chip-agent", `Agent ${portfolio.agentStatus || "offline"}`, agentTone);
    $("last-updated").textContent = new Date().toLocaleTimeString();
  }
  const session = fleet?.session;
  if (session) {
    const tones = { OPEN: "good", NEAR_CLOSE: "warn", PRE_MARKET: "info", CLOSED: null };
    let label = `Session ${session.state.replace("_", " ").toLowerCase()}`;
    if (session.state === "CLOSED" && session.seconds_to_open > 0) {
      const h = Math.floor(session.seconds_to_open / 3600);
      const m = Math.round((session.seconds_to_open % 3600) / 60);
      label += ` · opens in ${h}h ${m}m`;
    }
    if (session.state === "OPEN" && session.minutes_remaining > 0) {
      label += ` · ${Math.round(session.minutes_remaining)}m left`;
    }
    setChip("chip-session", label, tones[session.state]);
  } else if (portfolio) {
    setChip("chip-session", portfolio.marketOpen ? "Session open" : "Session closed", portfolio.marketOpen ? "good" : null);
  }
  if (strategy?.available) {
    const tones = { TRENDING: "good", RANGING: "info", VOLATILE: "warn" };
    setChip("chip-regime", `Regime ${strategy.regime.toLowerCase()}`, tones[strategy.regime] || "info");
  }
}

/* ------------------------- KPI tiles ------------------------------------ */

function renderKpis(p, analytics) {
  if (!p) return;
  $("kpi-nav").textContent = fmtMoney(p.nav);
  const dp = +p.dailyPnl || 0;
  const dpp = +p.dailyPnlPct || 0;
  $("kpi-nav-sub").textContent = `${fmtSigned(dp)} today`;
  $("kpi-nav-sub").className = `sub ${dp >= 0 ? "up" : "down"}`;

  $("kpi-daypnl").textContent = fmtSigned(dp);
  $("kpi-daypnl").className = `value num ${dp >= 0 ? "up" : "down"}`;
  $("kpi-daypnl-sub").textContent = `${fmtSignedPct(dpp)} of NAV`;

  $("kpi-cash").textContent = fmtMoney(p.cash);
  $("kpi-cash-sub").textContent = `buying power ${fmtMoney(p.buyingPower)}`;

  $("kpi-positions").textContent = p.openPositions;
  $("kpi-positions-sub").textContent = `${p.tradesToday || 0} trades today`;

  const wr = analytics?.strategy?.winRate ?? p.winRate ?? 0;
  $("kpi-winrate").textContent = fmtPct(wr);
  const pf = analytics?.strategy?.profitFactor;
  $("kpi-winrate-sub").textContent = pf != null ? `profit factor ${(+pf).toFixed(2)}` : "";

  const lt = +p.lifetimeRealizedPnl || 0;
  $("kpi-lifetime").textContent = fmtSigned(lt);
  $("kpi-lifetime").className = `value num ${lt >= 0 ? "up" : "down"}`;
}

/* ------------------------- ticker tape ---------------------------------- */

const livePrices = {};

function renderTape(items) {
  const track = $("tape-track");
  if (!track || !items?.length) return;
  const cell = (t) => {
    const chg = +(t.change ?? t.changePercent) || 0;
    const dir = chg >= 0 ? "up" : "down";
    const arrow = chg >= 0 ? "▲" : "▼";
    return `<span class="tape-item" data-sym="${esc(t.symbol)}">
      <span class="sym">${esc(t.symbol)}</span>
      <span class="num price">${fmtMoney(livePrices[t.symbol] ?? t.price)}</span>
      <span class="chg ${dir} num">${arrow} ${Math.abs(chg).toFixed(2)}%</span>
    </span>`;
  };
  const half = items.map(cell).join("");
  track.innerHTML = half + half; // duplicated for the seamless loop
  $("tape-summary").textContent =
    "Watchlist: " + items.map((t) => `${t.symbol} ${fmtMoney(t.price)}`).join(", ");
}

function patchTapePrice(symbol, price, wentUp) {
  document.querySelectorAll(`.tape-item[data-sym="${CSS.escape(symbol)}"] .price`).forEach((el) => {
    el.textContent = fmtMoney(price);
    el.classList.remove("flash-up", "flash-down");
    void el.offsetWidth;
    el.classList.add(wentUp ? "flash-up" : "flash-down");
  });
}

/* ------------------------- positions ------------------------------------ */

function renderPositions(positions) {
  const body = $("positions-body");
  if (!body) return;
  if (!positions?.length) {
    body.innerHTML = `<tr><td colspan="6" class="empty">No open positions</td></tr>`;
    return;
  }
  body.innerHTML = positions.map((p) => {
    const dir = p.pnl >= 0 ? "up" : "down";
    const arrow = p.pnl >= 0 ? "▲" : "▼";
    return `<tr>
      <td><button type="button" class="symbol-btn" data-stock="${esc(p.symbol)}">${esc(p.symbol)}</button></td>
      <td class="right num">${p.quantity}</td>
      <td class="right num">${fmtMoney(p.entryPrice)}</td>
      <td class="right num live-price" data-sym="${esc(p.symbol)}">${fmtMoney(p.currentPrice)}</td>
      <td class="right num live-pnl" data-sym="${esc(p.symbol)}" data-entry="${p.entryPrice}" data-qty="${p.quantity}">
        <span class="${dir}">${arrow} ${fmtSigned(p.pnl)} (${fmtSignedPct(p.pnlPct)})</span>
      </td>
      <td class="right num">${fmtMoney(p.trailingStop ?? p.stopLoss)}</td>
    </tr>`;
  }).join("");
}

function patchPositionPrice(symbol, price, wentUp) {
  const priceEl = document.querySelector(`.live-price[data-sym="${CSS.escape(symbol)}"]`);
  if (!priceEl) return;
  priceEl.textContent = fmtMoney(price);
  priceEl.classList.remove("flash-up", "flash-down");
  void priceEl.offsetWidth;
  priceEl.classList.add(wentUp ? "flash-up" : "flash-down");

  const pnlEl = document.querySelector(`.live-pnl[data-sym="${CSS.escape(symbol)}"]`);
  if (!pnlEl) return;
  const entry = parseFloat(pnlEl.dataset.entry);
  const qty = parseFloat(pnlEl.dataset.qty);
  if (!Number.isFinite(entry) || !Number.isFinite(qty) || qty <= 0) return;
  const pnl = (price - entry) * qty;
  const pct = ((price - entry) / entry) * 100;
  const dir = pnl >= 0 ? "up" : "down";
  const arrow = pnl >= 0 ? "▲" : "▼";
  pnlEl.innerHTML = `<span class="${dir}">${arrow} ${fmtSigned(pnl)} (${fmtSignedPct(pct)})</span>`;
}

function renderPending(orders) {
  const body = $("pending-body");
  if (!body) return;
  if (!orders?.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty">None</td></tr>`;
    return;
  }
  body.innerHTML = orders.map((o) => `<tr>
    <td>${esc(o.symbol)}</td>
    <td class="right num">${o.quantity}</td>
    <td class="right num">${fmtMoney(o.entryPrice)}</td>
    <td class="right num">${o.stopLoss ? fmtMoney(o.stopLoss) : "—"}</td>
    <td class="right num">${o.trailingPct ? fmtPct(o.trailingPct * 100, 1) : "—"}</td>
  </tr>`).join("");
}

/* ------------------------- vetting --------------------------------------- */

function renderVetting(v) {
  if (!v) return;
  const approvedEl = $("vetting-approved");
  const blockedEl = $("vetting-blocked");
  const blocklistEl = $("vetting-blocklist");
  const when = $("vetting-when");

  const vetted = v.vetted;
  if (vetted?.approved?.length) {
    approvedEl.innerHTML = vetted.approved
      .map((s) => `<span class="chip is-good"><span class="dot" aria-hidden="true"></span>${esc(s)}</span>`)
      .join("");
    if (when) when.textContent = `· ${esc(vetted.source || "")} ${vetted.session_date || ""}`;
  } else {
    approvedEl.innerHTML = `<span class="empty">Waiting for pre-market vetting…</span>`;
  }

  const blocked = Object.entries(vetted?.blocked || {});
  const reportBlocked = Object.entries(v.report?.results || {})
    .filter(([, r]) => r.verdict === "FAIL")
    .map(([sym, r]) => [sym, r.reason || `net return ${r.total_return_pct}% over ${r.n_trades} trades`]);
  const combined = blocked.length ? blocked : reportBlocked;
  blockedEl.innerHTML = combined.length
    ? combined.map(([sym, why]) => `<div class="block-item"><span class="badge blocked">${esc(sym)}</span><span class="why">${esc(why)}</span></div>`).join("")
    : `<p class="empty">None blocked</p>`;

  const bl = Object.entries(v.blocklist || {});
  blocklistEl.innerHTML = bl.length
    ? bl.map(([sym, info]) => `<div class="block-item"><span class="badge blocked">${esc(sym)}</span><span class="why">${esc(info.reason || "")} · until ${esc((info.until || "").slice(11, 16) || "next open")}</span></div>`).join("")
    : `<p class="empty">Nothing blocked in-session</p>`;
}

/* ------------------------- signals --------------------------------------- */

const sigState = { rows: [], filter: "ALL", sort: "mlConfidence", dir: -1, page: 1, perPage: 10 };

function recommendation(s) {
  const score = s.combinedScore ?? s.mlConfidence ?? 0;
  const thr = s.buyThreshold ?? 0.6;
  if (s.signal === "BUY") return { label: "BUY", cls: "buy" };
  if (s.signal === "SELL") return { label: "SELL", cls: "sell" };
  if (s.holdReason) return { label: "GATED", cls: "gated", why: s.holdReason };
  if (score >= thr - 0.05 && score < thr) return { label: "WARMING", cls: "warm" };
  return { label: "HOLD", cls: "" };
}

function confBar(v) {
  const pct = Math.max(0, Math.min(100, (+v || 0) * 100));
  return `<div class="conf"><div class="bar" aria-hidden="true"><span style="width:${pct}%"></span></div><span class="pct num">${pct.toFixed(0)}%</span></div>`;
}

function renderSignals() {
  const body = $("signals-body");
  if (!body) return;
  let rows = [...sigState.rows];

  if (sigState.filter !== "ALL") {
    rows = rows.filter((s) => {
      const rec = recommendation(s);
      if (sigState.filter === "BUY") return rec.label === "BUY";
      if (sigState.filter === "SELL") return rec.label === "SELL";
      if (sigState.filter === "WARM") return rec.label === "WARMING";
      if (sigState.filter === "GATED") return rec.label === "GATED";
      return true;
    });
  }
  rows.sort((a, b) => {
    const av = a[sigState.sort] ?? 0;
    const bv = b[sigState.sort] ?? 0;
    return (av > bv ? 1 : av < bv ? -1 : 0) * sigState.dir;
  });

  const pages = Math.max(1, Math.ceil(rows.length / sigState.perPage));
  sigState.page = Math.min(sigState.page, pages);
  const slice = rows.slice((sigState.page - 1) * sigState.perPage, sigState.page * sigState.perPage);

  body.innerHTML = slice.length
    ? slice.map((s) => {
        const rec = recommendation(s);
        const why = rec.why ? ` title="${esc(rec.why)}"` : "";
        return `<tr>
          <td><button type="button" class="symbol-btn" data-stock="${esc(s.symbol)}">${esc(s.symbol)}</button></td>
          <td><span class="badge ${rec.cls}"${why}>${rec.label}</span>${rec.why ? `<div class="why" style="font-size:0.7rem;color:var(--muted);margin-top:2px;max-width:260px;">${esc(rec.why.slice(0, 90))}</div>` : ""}</td>
          <td>${confBar(s.mlConfidence)}</td>
          <td>${confBar(s.mlConfidenceSwing)}</td>
          <td class="right num">${s.buyThreshold != null ? (s.buyThreshold * 100).toFixed(0) + "%" : "—"}</td>
          <td class="right num">${fmtMoney(s.price)}</td>
        </tr>`;
      }).join("")
    : `<tr><td colspan="6" class="empty">No signals match this filter</td></tr>`;

  $("sig-page").textContent = `Page ${sigState.page} of ${pages}`;
  $("sig-prev").disabled = sigState.page <= 1;
  $("sig-next").disabled = sigState.page >= pages;
}

function initSignalControls() {
  document.querySelectorAll(".filters button[data-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filters button[data-filter]").forEach((b) => b.setAttribute("aria-pressed", "false"));
      btn.setAttribute("aria-pressed", "true");
      sigState.filter = btn.dataset.filter;
      sigState.page = 1;
      renderSignals();
    });
  });
  document.querySelectorAll("#signals-table th button[data-sort]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const col = btn.dataset.sort;
      if (sigState.sort === col) sigState.dir *= -1;
      else { sigState.sort = col; sigState.dir = -1; }
      document.querySelectorAll("#signals-table th").forEach((th) => th.setAttribute("aria-sort", "none"));
      btn.closest("th").setAttribute("aria-sort", sigState.dir === -1 ? "descending" : "ascending");
      document.querySelectorAll("#signals-table th button span").forEach((sp) => (sp.textContent = ""));
      btn.querySelector("span").textContent = sigState.dir === -1 ? "▼" : "▲";
      renderSignals();
    });
  });
  $("sig-prev").addEventListener("click", () => { sigState.page--; renderSignals(); });
  $("sig-next").addEventListener("click", () => { sigState.page++; renderSignals(); });
}

/* ------------------------- trades ---------------------------------------- */

function renderTrades(trades) {
  const body = $("trades-body");
  if (!body) return;
  if (!trades?.length) {
    body.innerHTML = `<tr><td colspan="4" class="empty">No recent trades</td></tr>`;
    return;
  }
  body.innerHTML = trades.slice(0, 30).map((t) => {
    const isBuy = t.action === "BUY";
    let detail;
    if (isBuy) {
      detail = `<span class="num">Entry ${fmtMoney(t.price)} · qty ${t.quantity}</span>`;
    } else {
      const pnl = parseFloat(t.pnl) || 0;
      const dir = pnl >= 0 ? "up" : "down";
      const arrow = pnl >= 0 ? "▲" : "▼";
      const reason = t.exit_reason ? ` <span class="badge" style="font-size:0.65rem;">${esc(String(t.exit_reason).replace(/_/g, " "))}</span>` : "";
      detail = `<span class="num ${dir}">${arrow} ${fmtSigned(pnl)}</span> <span class="num" style="color:var(--muted)">@ ${fmtMoney(t.price)}</span>${reason}`;
    }
    return `<tr>
      <td class="num" style="color:var(--muted)">${esc(t.date ? `${t.date.slice(5)} ` : "")}${esc(t.time || "")}</td>
      <td><button type="button" class="symbol-btn" data-stock="${esc(t.symbol)}">${esc(t.symbol)}</button></td>
      <td><span class="badge ${isBuy ? "buy" : "sell"}">${esc(t.action)}</span></td>
      <td class="right">${detail}</td>
    </tr>`;
  }).join("");
}

/* ------------------------- fleet ------------------------------------------ */

function renderFleet(fleet) {
  const grid = $("fleet-grid");
  if (!grid) return;
  if (!fleet?.available || !fleet.agents?.length) {
    grid.innerHTML = `<span class="empty">Bus unavailable</span>`;
    return;
  }
  grid.innerHTML = fleet.agents.map((a) => {
    const cls = !a.alive ? "down" : a.status === "busy" ? "busy" : "alive";
    const state = !a.alive ? "down" : a.status;
    return `<div class="agent ${cls}"><span class="dot" aria-hidden="true"></span>${esc(a.name)}<span class="state">${esc(state)}</span></div>`;
  }).join("");
}

function renderExitReasons(breakdown) {
  // breakdown: [{reason, wins, losses, total, winRate, pnl}, …]
  const body = $("exit-body");
  if (!body) return;
  if (!Array.isArray(breakdown) || !breakdown.length) {
    body.innerHTML = `<tr><td colspan="4" class="empty">No closed trades yet</td></tr>`;
    return;
  }
  const rows = [...breakdown].sort((a, b) => b.pnl - a.pnl);
  body.innerHTML = rows.map((d) => {
    const dir = d.pnl >= 0 ? "up" : "down";
    return `<tr>
      <td>${esc(String(d.reason).replace(/_/g, " "))}</td>
      <td class="right num">${d.wins}</td>
      <td class="right num">${d.losses}</td>
      <td class="right num"><span class="${dir}">${fmtSigned(d.pnl)}</span></td>
    </tr>`;
  }).join("");
}

/* ------------------------- logs -------------------------------------------- */

function renderLogs(lines) {
  const box = $("log-box");
  if (!box || !Array.isArray(lines)) return;
  box.textContent = lines.join("\n");
  box.scrollTop = box.scrollHeight;
}

/* ------------------------- stock modal -------------------------------------- */

async function openStockModal(symbol) {
  const modal = $("stock-modal");
  $("modal-title").textContent = symbol;
  modal.classList.add("open");
  modal.querySelector(".modal-close").focus();
  try {
    const res = await fetch(api(`/api/stock/${encodeURIComponent(symbol)}`));
    const data = await res.json();
    if (stockChart) stockChart.destroy();
    const rows = (data.chartData || []).filter((d) => d.price != null);
    stockChart = new Chart($("stock-chart"), {
      type: "line",
      data: {
        datasets: [{
          label: symbol,
          data: rows.map((d) => ({ x: d.date, y: d.price })),
          borderColor: cssVar("--s1"),
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: { ...tooltipStyle(), callbacks: { label: (i) => ` ${fmtMoney(i.parsed.y)}` } } },
        scales: baseScales(true),
      },
    });
    const s = data.summary || {};
    $("modal-bought").textContent = fmtMoney(s.totalBought || 0);
    $("modal-sold").textContent = fmtMoney(s.totalSold || 0);
    const pnl = s.totalPnl || 0;
    $("modal-pnl").textContent = fmtSigned(pnl);
    $("modal-pnl").className = `value num ${pnl >= 0 ? "up" : "down"}`;
    const trades = data.trades || [];
    $("modal-trades").innerHTML = trades.length
      ? trades.slice(0, 25).map((t) => `<tr>
          <td class="num">${esc(t.date)}</td>
          <td><span class="badge ${t.action === "BUY" ? "buy" : "sell"}">${esc(t.action)}</span></td>
          <td class="right num">${fmtMoney(t.price)}</td>
          <td class="right num">${t.quantity}</td>
        </tr>`).join("")
      : `<tr><td colspan="4" class="empty">No transactions</td></tr>`;
  } catch {
    $("modal-trades").innerHTML = `<tr><td colspan="4" class="empty">Failed to load</td></tr>`;
  }
}

function initModal() {
  const modal = $("stock-modal");
  modal.querySelector(".modal-close").addEventListener("click", () => modal.classList.remove("open"));
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.remove("open"); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") modal.classList.remove("open"); });
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-stock]");
    if (btn) openStockModal(btn.dataset.stock);
  });
}

/* ------------------------- SSE live stream ----------------------------------- */

function initStream() {
  let source;
  const connect = () => {
    source = new EventSource(new URL("/api/stream", location.origin).href);
    source.onopen = () => setChip("chip-live", "Live feed", "good");
    source.onerror = () => setChip("chip-live", "Feed reconnecting", "warn");
    source.onmessage = (event) => {
      try {
        const tick = JSON.parse(event.data);
        if (!tick.symbol || !tick.price) return;
        const prev = livePrices[tick.symbol] ?? tick.price;
        livePrices[tick.symbol] = tick.price;
        if (tick.price === prev) return;
        const wentUp = tick.price > prev;
        patchTapePrice(tick.symbol, tick.price, wentUp);
        patchPositionPrice(tick.symbol, tick.price, wentUp);
      } catch { /* malformed tick — skip */ }
    };
  };
  connect();
}

/* ------------------------- range toggles -------------------------------------- */

function initRangeToggles() {
  document.querySelectorAll(".range-toggles button[data-range]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document.querySelectorAll(".range-toggles button[data-range]").forEach((b) => b.setAttribute("aria-pressed", "false"));
      btn.setAttribute("aria-pressed", "true");
      navRange = btn.dataset.range;
      await refreshNav();
    });
  });
}

/* ------------------------- fetch cycles ----------------------------------------- */

/* Resolve against origin so pages opened as http://user:pass@host still work
   (credentialed base URLs make relative fetch() throw). */
const api = (p) => new URL(p, location.origin).href;
const getJSON = (url) => fetch(api(url)).then((r) => (r.ok ? r.json() : null)).catch(() => null);

async function refreshNav() {
  const nav = await getJSON(`/api/nav-history?range=${navRange}`);
  if (nav?.history?.length) renderNavChart(nav.history);
  else if (Array.isArray(nav) && nav.length) renderNavChart(nav);
}

async function refreshCore() {
  const [portfolio, positions, signals, trades, analytics, fleet, strategy] = await Promise.all([
    getJSON("/api/portfolio"),
    getJSON("/api/positions"),
    getJSON("/api/signals"),
    getJSON("/api/trades"),
    getJSON("/api/analytics"),
    getJSON("/api/fleet"),
    getJSON("/api/strategy"),
  ]);
  renderHeader(portfolio, fleet, strategy);
  renderKpis(portfolio, analytics);
  renderPositions(positions);
  renderTrades(trades);
  renderFleet(fleet);
  if (Array.isArray(signals)) {
    sigState.rows = signals;
    renderSignals();
  }
}

async function refreshSlow() {
  const [ticker, vetting, pending, dailyPnl, mlAcc, logs] = await Promise.all([
    getJSON("/api/ticker"),
    getJSON("/api/vetting"),
    getJSON("/api/pending-orders"),
    getJSON("/api/daily-pnl"),
    getJSON("/api/ml-accuracy"),
    getJSON("/api/logs"),
  ]);
  if (ticker?.ticker) renderTape(ticker.ticker);
  renderVetting(vetting);
  renderPending(pending);
  if (Array.isArray(dailyPnl) && dailyPnl.length) renderPnlChart(dailyPnl);
  if (mlAcc && !mlAcc.error) {
    renderCalibChart(mlAcc.calibration);
    renderExitReasons(mlAcc.exitBreakdown);
  }
  renderLogs(logs);
}

/* ------------------------- boot -------------------------------------------------- */

document.addEventListener("DOMContentLoaded", async () => {
  initTheme();
  chartDefaults();
  initSignalControls();
  initRangeToggles();
  initModal();
  initStream();

  const cfg = await getJSON("/api/market-config");
  if (cfg?.market) MARKET = cfg.market;
  $("market-tag").textContent = MARKET === "IN" ? "NSE · India" : "NASDAQ · US";
  $("footer-market").textContent = MARKET === "IN" ? "Indian market (₹)" : "US market ($)";

  await Promise.all([refreshCore(), refreshSlow(), refreshNav()]);
  setInterval(refreshCore, 5000);
  setInterval(refreshSlow, 30000);
  setInterval(refreshNav, 60000);
});
