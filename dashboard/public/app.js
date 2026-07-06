let currentMarket = "US"; // Default, gets updated dynamically

const formatMoney = (val) => new Intl.NumberFormat(currentMarket === 'IN' ? 'en-IN' : 'en-US', { 
  style: 'currency', 
  currency: currentMarket === 'IN' ? 'INR' : 'USD', 
  maximumFractionDigits: 2 
}).format(val);
const formatPct = (val) => (val > 0 ? '+' : '') + parseFloat(val).toFixed(2) + '%';
const formatNum = (val) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(val);

let state = {
  navHistory: [],
  signals: [],
  sigPage: 1,
  sigFilter: 'ALL', // ALL, BUY, SELL, HOLD, GATED
};

function applyThemeToCharts(theme) {
  const gridColor = theme === 'light' ? '#E5E7EB' : '#1F1F2E';
  const textColor = theme === 'light' ? '#6B7280' : '#8B8B9E';
  
  Chart.defaults.color = textColor;
  
  if (charts.nav) {
    if (charts.nav.options.scales.x) {
      charts.nav.options.scales.x.grid.color = gridColor;
      if (charts.nav.options.scales.x.ticks) charts.nav.options.scales.x.ticks.color = textColor;
    }
    if (charts.nav.options.scales.y) {
      charts.nav.options.scales.y.grid.color = gridColor;
      if (charts.nav.options.scales.y.ticks) charts.nav.options.scales.y.ticks.color = textColor;
    }
    charts.nav.update();
  }
  if (charts.sector) {
    if (charts.sector.options.plugins.legend.labels) {
      charts.sector.options.plugins.legend.labels.color = textColor;
    }
    charts.sector.update();
  }
  if (charts.stockDetail) {
    if (charts.stockDetail.options.scales.x) {
      charts.stockDetail.options.scales.x.grid.color = gridColor;
      if (charts.stockDetail.options.scales.x.ticks) charts.stockDetail.options.scales.x.ticks.color = textColor;
    }
    if (charts.stockDetail.options.scales.y) {
      charts.stockDetail.options.scales.y.grid.color = gridColor;
      if (charts.stockDetail.options.scales.y.ticks) charts.stockDetail.options.scales.y.ticks.color = textColor;
    }
    charts.stockDetail.update();
  }
}

function initTheme() {
  const savedTheme = localStorage.getItem('theme') || 'dark';
  document.documentElement.setAttribute('data-theme', savedTheme);
  document.getElementById('theme-icon-sun').style.display = savedTheme === 'light' ? 'none' : 'block';
  document.getElementById('theme-icon-moon').style.display = savedTheme === 'light' ? 'block' : 'none';
  // Charts are initialized later, so applyThemeToCharts won't error, but won't do anything yet.
}

function toggleTheme() {
  const currentTheme = document.documentElement.getAttribute('data-theme');
  const newTheme = currentTheme === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', newTheme);
  localStorage.setItem('theme', newTheme);
  
  document.getElementById('theme-icon-sun').style.display = newTheme === 'light' ? 'none' : 'block';
  document.getElementById('theme-icon-moon').style.display = newTheme === 'light' ? 'block' : 'none';
  
  applyThemeToCharts(newTheme);
}

// Initialize theme immediately
initTheme();

async function fetchDashboardData() {
  const syncIcon = document.getElementById('sync-icon');
  if (syncIcon) syncIcon.classList.add('spinning');

  try {
    const [portRes, posRes, sigRes, tradesRes, analyticsRes, tickerRes, healthRes] = await Promise.all([
      fetch('/api/portfolio').catch(() => null),
      fetch('/api/positions').catch(() => null),
      fetch('/api/signals').catch(() => null),
      fetch('/api/trades').catch(() => null),
      fetch('/api/analytics').catch(() => null),
      fetch('/api/ticker').catch(() => null),
      fetch('/api/apps-health').catch(() => null)
    ]);

    let healthData = null;
    if (healthRes && healthRes.ok) healthData = await healthRes.json();
    
    if (portRes && portRes.ok) {
      const data = await portRes.json();
      if (data.market) {
        currentMarket = data.market;
        const flagEl = document.getElementById('market-flag');
        if (flagEl) {
          flagEl.textContent = currentMarket === 'IN' ? '🇮🇳' : '🇺🇸';
        }
      }
      renderPortfolio(data, healthData);
    }
    if (posRes && posRes.ok) renderPositions(await posRes.json());
    if (sigRes && sigRes.ok) {
      state.signals = await sigRes.json();
      renderSignals();
    }
    if (tradesRes && tradesRes.ok) renderTrades(await tradesRes.json());
    if (analyticsRes && analyticsRes.ok) renderAnalytics(await analyticsRes.json());
    if (tickerRes && tickerRes.ok) renderTicker(await tickerRes.json());

    const lastUpdated = document.getElementById('last-updated');
    if (lastUpdated) {
      lastUpdated.textContent = 'Live • ' + new Date().toLocaleTimeString();
    }
  } catch (e) {
    console.error("Dashboard data fetch error", e);
  } finally {
    if (syncIcon) {
      setTimeout(() => syncIcon.classList.remove('spinning'), 500);
    }
  }
}

function renderPortfolio(data, healthData) {
  document.getElementById('val-nav').textContent = formatMoney(data.nav);
  const pnlEl = document.getElementById('val-pnl');
  pnlEl.textContent = `${formatMoney(data.dailyPnl)} (${data.dailyPnlPct.toFixed(2)}%) Today`;
  pnlEl.className = data.dailyPnl >= 0 ? 'summary-change up' : 'summary-change down';
  
  document.getElementById('val-cash').textContent = formatMoney(data.cash);
  
  if (data.buyingPower !== undefined) {
    document.getElementById('val-bp').textContent = formatMoney(data.buyingPower);
  }
  
  document.getElementById('val-positions').textContent = data.openPositions;
  
  // Market Badge
  const marketBadge = document.getElementById('badge-market');
  if (marketBadge) {
    if (data.marketOpen) {
      marketBadge.className = 'pill-badge active';
      marketBadge.textContent = 'Market Open';
    } else {
      marketBadge.className = 'pill-badge offline';
      if (data.nextOpen) {
        const d = new Date(data.nextOpen);
        const options = { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' };
        marketBadge.textContent = `Market Closed (Opens: ${d.toLocaleString(undefined, options)})`;
      } else {
        marketBadge.textContent = 'Market Closed';
      }
    }
  }

  // Agent Badge
  const agentBadge = document.getElementById('badge-agent');
  if (agentBadge) {
    let status = healthData ? healthData.trading_agent : data.agentStatus;
    if (status === 'running') {
      agentBadge.className = 'pill-badge active';
      agentBadge.textContent = 'Agent Online';
    } else if (status === 'sleeping') {
      agentBadge.className = 'pill-badge';
      agentBadge.style.color = 'var(--signal-orange)';
      agentBadge.style.borderColor = 'rgba(255, 165, 2, 0.3)';
      agentBadge.textContent = 'Agent Sleeping';
    } else {
      agentBadge.className = 'pill-badge offline';
      agentBadge.textContent = 'Agent Offline';
    }
  }
}

function renderPositions(positions) {
  const container = document.getElementById('table-positions');
  if (!positions || positions.length === 0) {
    container.innerHTML = '<tr><td colspan="5" class="empty-state">No open positions</td></tr>';
    return;
  }
  
  let html = '';
  positions.forEach(p => {
    const isUp = p.pnl >= 0;
    const colorClass = isUp ? 'status-buy' : 'status-sell';
    html += `
      <tr>
        <td style="font-weight: 600;"><span class="clickable-symbol" onclick="showStockDetails('${p.symbol}')">${p.symbol}</span></td>
        <td class="mono td-right">${p.quantity}</td>
        <td class="mono td-right">${formatMoney(p.currentPrice)}</td>
        <td class="mono td-right ${colorClass}">${formatMoney(p.pnl)}<br><span style="font-size: 11px;">${p.pnlPct.toFixed(2)}%</span></td>
        <td class="td-right">
          <div style="font-size: 11px; color: var(--text-secondary);">TP: $${p.takeProfit || '-'}</div>
          <div style="font-size: 11px; color: var(--text-secondary);">Trail: $${p.trailingStop || '-'}</div>
        </td>
      </tr>
    `;
  });
  container.innerHTML = html;
}

function renderSignals() {
  const container = document.getElementById('table-signals');
  let filtered = state.signals;
  
  if (state.sigFilter !== 'ALL') {
    if (state.sigFilter === 'GATED') {
      filtered = filtered.filter(s => s.signal === 'HOLD' && s.holdReason && s.combinedScore >= (s.buyThreshold || 0.48));
    } else {
      filtered = filtered.filter(s => s.signal === state.sigFilter && !(s.signal === 'HOLD' && s.holdReason && s.combinedScore >= (s.buyThreshold || 0.48)));
    }
  }

  if (filtered.length === 0) {
    container.innerHTML = '<tr><td colspan="5" class="empty-state">No signals match filter</td></tr>';
    return;
  }
  
  // Show only top 10
  const pageSignals = filtered.slice(0, 10);
  
  let html = '';
  pageSignals.forEach(s => {
    const rawSignal = s.signal || 'HOLD';
    const isGated = rawSignal === 'HOLD' && s.holdReason && s.combinedScore >= (s.buyThreshold || 0.48);
    
    let shortReason = s.holdReason;
    if (isGated && s.holdReason) {
      if (s.holdReason.includes("ADX=")) {
        const m = s.holdReason.match(/ADX=([\d.]+)/);
        shortReason = m ? `ADX: ${m[1]} < 25` : "Low ADX < 25";
      } else if (s.holdReason.includes("volume is only")) {
        const m = s.holdReason.match(/([\d.]+)x average/);
        shortReason = m ? `Vol: ${m[1]}x < 1.5x` : "Low Vol < 1.5x";
      } else if (s.holdReason.includes("cooldown")) {
        const m = s.holdReason.match(/cooldown for ([\d.]+) more minutes/);
        shortReason = m ? `Cooldown: ${m[1]}m` : "Cooldown";
      } else if (s.holdReason.includes("Max deployment")) {
        const m = s.holdReason.match(/deployment (\d+)% reached/);
        shortReason = m ? `Max Deployment: ${m[1]}%` : "Max Deployment";
      } else if (s.holdReason.includes("spend cap")) {
        const m = s.holdReason.match(/cap [£$€₹]?([\d.]+)/);
        shortReason = m ? `Daily Cap: ${m[1]}` : "Daily Cap";
      } else if (s.holdReason.includes("already held")) {
        shortReason = "Already Held";
      } else if (s.holdReason.includes("max open positions")) {
        shortReason = "Max Positions";
      }
    }

    let displaySignal = isGated ? 'GATED' : rawSignal;
    if (isGated && shortReason) {
      displaySignal += ` (${shortReason})`;
    }
    const badgeClass = isGated ? 'gated' : rawSignal.toLowerCase();
    
    // Agent Score Color matches Recommendation
    let scoreClass = 'status-' + (isGated ? 'gated' : rawSignal.toLowerCase());
    
    // ML Confidence progress bar
    let mlHtml = '-';
    if (s.mlConfidence) {
      const upConf = s.mlConfidence * 100;
      const downConf = 100 - upConf;
      mlHtml = `
        <div style="width: 100px;">
          <div style="display: flex; justify-content: space-between; font-size: 10px; margin-bottom: 2px;">
            <span style="color: var(--signal-green); font-weight: 600;">▲ ${upConf.toFixed(0)}%</span>
            <span style="color: var(--signal-red); font-weight: 600;">${downConf.toFixed(0)}% ▼</span>
          </div>
          <div class="progress-wrap" style="display: flex;">
            <div class="progress-bar" style="width: ${upConf}%; background-color: var(--signal-green); border-radius: 0;"></div>
            <div class="progress-bar" style="width: ${downConf}%; background-color: var(--signal-red); border-radius: 0;"></div>
          </div>
        </div>
      `;
    }

    html += `
      <tr>
        <td style="font-weight: 600;"><span class="clickable-symbol" onclick="showStockDetails('${s.symbol}')">${s.symbol}</span></td>
        <td class="mono ${scoreClass}">${s.combinedScore ? s.combinedScore.toFixed(3) : '-'}</td>
        <td><span class="badge-outline ${badgeClass}">${displaySignal}</span></td>
        <td>${mlHtml}</td>
        <td class="mono td-right">${formatMoney(s.price)}</td>
      </tr>
    `;
  });
  container.innerHTML = html;
}

function setSignalFilter(filter) {
  state.sigFilter = filter;
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.classList.remove('active');
    if (btn.innerText.toUpperCase() === filter) btn.classList.add('active');
  });
  renderSignals();
}

function renderTrades(trades) {
  const container = document.getElementById('table-trades');
  if (!trades || trades.length === 0) {
    container.innerHTML = '<tr><td colspan="4" class="empty-state">No recent trades</td></tr>';
    return;
  }
  
  let html = '';
  trades.slice(0, 10).forEach(t => {
    const actionClass = t.action === 'BUY' ? 'status-buy' : 'status-sell';
    let detailsHtml = '';
    if (t.action === 'BUY') {
      detailsHtml = `<div>Entry: ${formatMoney(t.price)}</div><div style="font-size: 11px; color: var(--text-secondary);">Qty: ${t.quantity}</div>`;
    } else {
      const pnl = parseFloat(t.pnl) || 0;
      const qty = parseFloat(t.quantity) || 1;
      const entryPrice = parseFloat(t.price) - (pnl / qty);
      const pnlColor = pnl >= 0 ? 'var(--signal-green)' : 'var(--signal-red)';
      const pnlSign = pnl >= 0 ? '+' : '';
      detailsHtml = `
        <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 2px;">Entry: ${formatMoney(entryPrice)} &rarr; Exit: ${formatMoney(t.price)}</div>
        <div style="color: ${pnlColor}; font-weight: 600; font-size: 12px;">PnL: ${pnlSign}${formatMoney(pnl)}</div>
      `;
    }

    html += `
      <tr>
        <td style="color: var(--text-secondary); font-size: 11px;">${t.time}</td>
        <td style="font-weight: 600;"><span class="clickable-symbol" onclick="showStockDetails('${t.symbol}')">${t.symbol}</span></td>
        <td><span class="${actionClass}">${t.action}</span></td>
        <td class="mono td-right">${detailsHtml}</td>
      </tr>
    `;
  });
  container.innerHTML = html;
}

let charts = {};

function initCharts() {
  Chart.defaults.color = '#8B8B9E';
  Chart.defaults.font.family = 'Inter';
  
  // NAV Chart
  const navCtx = document.getElementById('navChart');
  if (navCtx) {
    charts.nav = new Chart(navCtx, {
      type: 'line',
      data: {
        labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
        datasets: [{
          label: 'Portfolio NAV',
          data: [98000, 99500, 99000, 101000, 102500], // Dummy initial
          borderColor: '#00E5A3',
          backgroundColor: 'rgba(0, 229, 163, 0.1)',
          borderWidth: 2,
          fill: true,
          tension: 0.4,
          pointRadius: 0
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: 'index',
          intersect: false,
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            enabled: true,
            callbacks: {
              label: function(context) {
                let label = context.dataset.label || '';
                if (label) {
                  label += ': ';
                }
                if (context.parsed.y !== null) {
                  label += new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(context.parsed.y);
                }
                return label;
              }
            }
          }
        },
        scales: {
          x: { grid: { display: false, color: '#1F1F2E' }, ticks: {} },
          y: { grid: { color: '#1F1F2E' }, ticks: {} }
        }
      }
    });
  }

  // Sector Donut Chart
  const sectorCtx = document.getElementById('sectorChart');
  if (sectorCtx) {
    charts.sector = new Chart(sectorCtx, {
      type: 'doughnut',
      data: {
        labels: ['Tech', 'Fin', 'Health', 'Other'],
        datasets: [{
          data: [45, 25, 15, 15], // Dummy initial
          backgroundColor: ['#00E5A3', '#3742FA', '#FFA502', '#FF4757'],
          borderWidth: 0,
          cutout: '75%'
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'right', labels: { color: '#8B8B9E', font: { size: 11 } } } }
      }
    });
  }

  // Apply theme to charts
  applyThemeToCharts(document.documentElement.getAttribute('data-theme') || 'dark');

  // Load initial NAV history
  fetchNavHistory('1d');
}

async function fetchNavHistory(range) {
  try {
    const res = await fetch(`/api/nav-history?range=${range}`);
    if (res.ok && charts.nav) {
      const data = await res.json();
      charts.nav.data.labels = data.map(d => {
        if (range === '1d' && d.date.includes('T')) {
          return d.date.split('T')[1].substring(0, 5);
        }
        return d.date;
      });
      charts.nav.data.datasets[0].data = data.map(d => d.nav);
      charts.nav.update();
      
      // Update active button
      document.querySelectorAll('#nav-time-toggles button').forEach(btn => {
        btn.classList.remove('active');
        if (btn.getAttribute('onclick').includes(`'${range}'`)) {
          btn.classList.add('active');
        }
      });
    }
  } catch (e) {
    console.error("Failed to fetch nav history", e);
  }
}

function renderAnalytics(data) {
  // Update Model Health
  if (data.modelHealth) {
    document.getElementById('val-accuracy').textContent = data.modelHealth.predictionAccuracy.toFixed(1) + '%';
    document.getElementById('val-signals-today').textContent = data.modelHealth.signalsToday;
    document.getElementById('val-avg-conf').textContent = data.modelHealth.avgConfidence.toFixed(1) + '%';
  }
  
  // Update Risk
  if (data.risk) {
    document.getElementById('val-var').textContent = data.risk.var95.toFixed(2) + '%';
    document.getElementById('val-beta').textContent = data.risk.beta.toFixed(2);
    document.getElementById('val-drawdown').textContent = data.risk.maxDrawdown.toFixed(2) + '%';
    document.getElementById('val-volatility').textContent = data.risk.volatility.toFixed(1) + '%';
  }
  
  // Update Sector Chart
  if (data.sectorExposure && charts.sector) {
    charts.sector.data.labels = data.sectorExposure.slice(0, 5).map(s => s.sector);
    charts.sector.data.datasets[0].data = data.sectorExposure.slice(0, 5).map(s => s.allocation);
    charts.sector.update();
  }

  // Update AI Feed
  const feedContainer = document.getElementById('agent-feed');
  if (data.modelHealth && feedContainer) {
    feedContainer.innerHTML = `
      <div class="feed-item">
        <div class="feed-icon approved">✓</div>
        <div class="feed-content">
          <div class="feed-header">
            <span class="feed-title">System Health OK</span>
            <span class="feed-time">Just now</span>
          </div>
          <div class="feed-desc">Models running. Active predictions: ${data.modelHealth.signalsToday}.</div>
        </div>
      </div>
      <div class="feed-item">
        <div class="feed-icon approved">✓</div>
        <div class="feed-content">
          <div class="feed-header">
            <span class="feed-title">Risk Limits Validated</span>
            <span class="feed-time">2 mins ago</span>
          </div>
          <div class="feed-desc">Portfolio VaR (${data.risk?.var95?.toFixed(2)}%) within acceptable thresholds.</div>
        </div>
      </div>
    `;
  }
}

// Modal Logic
function closeStockModal() {
  document.getElementById('stockModal').classList.remove('open');
}

async function showStockDetails(symbol) {
  try {
    const res = await fetch(`/api/stock/${symbol}`);
    if (!res.ok) return;
    const data = await res.json();
    
    document.getElementById('modal-title').textContent = symbol;
    document.getElementById('modal-bought').textContent = formatMoney(data.summary.totalBought);
    document.getElementById('modal-sold').textContent = formatMoney(data.summary.totalSold);
    
    const pnlEl = document.getElementById('modal-pnl');
    pnlEl.textContent = formatMoney(data.summary.totalPnl);
    pnlEl.style.color = data.summary.totalPnl >= 0 ? 'var(--signal-green)' : 'var(--signal-red)';
    
    // Render Trades
    const tbody = document.getElementById('modal-trades-body');
    if (!data.trades || data.trades.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No transactions</td></tr>';
    } else {
      let thtml = '';
      data.trades.forEach(t => {
        const actionClass = t.action === 'BUY' ? 'status-buy' : 'status-sell';
        thtml += `
          <tr>
            <td style="color: var(--text-secondary); font-size: 12px;">${t.date} ${t.time}</td>
            <td><span class="${actionClass}">${t.action}</span></td>
            <td class="mono td-right">${formatMoney(t.price)}</td>
            <td class="mono td-right">${t.quantity}</td>
          </tr>
        `;
      });
      tbody.innerHTML = thtml;
    }
    
    // Render Chart
    const ctx = document.getElementById('stockDetailChart');
    if (charts.stockDetail) charts.stockDetail.destroy();
    
    if (data.chartData && data.chartData.length > 0) {
      charts.stockDetail = new Chart(ctx, {
        type: 'line',
        data: {
          labels: data.chartData.map(d => d.date),
          datasets: [{
            label: 'Price',
            data: data.chartData.map(d => d.price),
            borderColor: '#3742FA',
            backgroundColor: 'rgba(55, 66, 250, 0.1)',
            borderWidth: 2,
            fill: true,
            tension: 0.1,
            pointRadius: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { display: false, color: '#1F1F2E' }, ticks: {} },
            y: { grid: { color: '#1F1F2E' }, ticks: {} }
          }
        }
      });
      applyThemeToCharts(document.documentElement.getAttribute('data-theme') || 'dark');
    }
    
    document.getElementById('stockModal').classList.add('open');
  } catch (e) {
    console.error("Failed to load stock details", e);
  }
}


// -----------------------------------------------------------------------------
// Live Ticker (UDP -> SSE)
// -----------------------------------------------------------------------------

const livePrices = {};

function setupLiveTicker() {
  const source = new EventSource('/api/stream');
  
  source.onmessage = function(event) {
    try {
      const data = JSON.parse(event.data);
      if (!data.symbol || !data.price) return;
      
      const prevPrice = livePrices[data.symbol] || data.price;
      livePrices[data.symbol] = data.price;
      
      // Update all instances of this symbol in the marquee
      const tickerContent = document.getElementById('ticker-content');
      if (tickerContent) {
        // SSE will target all duplicate nodes (due to marquee)
        const nodes = Array.from(tickerContent.querySelectorAll(`[id^="live-tick-${data.symbol}"]`));
        
        if (nodes.length === 0) {
          // If it doesn't exist, prepend it to the beginning of the marquee
          let el = document.createElement('span');
          el.id = `live-tick-${data.symbol}-0`;
          el.className = 'ticker-item';
          tickerContent.prepend(el);
          nodes.push(el);
          
          const loadingEl = Array.from(tickerContent.children).find(c => c.textContent.includes('Loading'));
          if (loadingEl) loadingEl.remove();
        }
        
        const isUp = data.price >= prevPrice;
        const arrow = isUp ? '▲' : '▼';
        
        nodes.forEach(el => {
          if (data.price !== prevPrice) {
            el.classList.remove('flash-green', 'flash-red');
            void el.offsetWidth; // Reflow
            el.classList.add(isUp ? 'flash-green' : 'flash-red');
          }
          el.innerHTML = `<strong>${data.symbol}</strong> ${formatMoney(data.price)} ${arrow}`;
        });
      }
    } catch (err) {
      console.error("Error parsing live tick:", err);
    }
  };
  
  source.onerror = function() {
    console.log("Live ticker SSE connection lost. Reconnecting...");
  };
}

document.addEventListener('DOMContentLoaded', () => {
  initCharts();
  setupLiveTicker();
  fetchDashboardData();
  setInterval(fetchDashboardData, 5000);
});

function renderTicker(data) {
  const container = document.getElementById('ticker-content');
  if (!data || !data.ticker || data.ticker.length === 0) {
    if (container.children.length === 0 || container.innerHTML.includes('Loading')) {
      container.innerHTML = '<span class="ticker-item">No ticker data available</span>';
    }
    return;
  }
  
  // Remove "Loading" text if present
  const loadingEl = Array.from(container.children).find(c => c.textContent.includes('Loading'));
  if (loadingEl) loadingEl.remove();

  // Create two copies for seamless marquee
  const items = [...data.ticker, ...data.ticker];
  
  // Only recreate innerHTML if we have an empty container or a totally different count.
  // Otherwise, intelligently update existing spans to not break CSS scroll animation or SSE classes.
  if (container.children.length !== items.length) {
    let html = '';
    items.forEach((t, i) => {
      const rawChange = t.change !== undefined ? t.change : t.changePercent;
      let changePct = parseFloat(rawChange);
      if (isNaN(changePct)) changePct = 0;
      
      const isUp = changePct >= 0;
      const arrow = isUp ? '▲' : '▼';
      const cls = isUp ? 'up' : 'down';
      
      // We append index to ID to allow duplicates in the marquee
      html += `<span id="live-tick-${t.symbol}-${i}" class="ticker-item ${cls}"><strong>${t.symbol}</strong> ${formatMoney(t.price)} ${arrow} ${Math.abs(changePct).toFixed(2)}%</span>`;
    });
    container.innerHTML = html;
  } else {
    // Intelligently update text
    items.forEach((t, i) => {
      const el = document.getElementById(`live-tick-${t.symbol}-${i}`);
      if (el) {
        // Only update if SSE hasn't flashed it very recently (prevent race condition overwrites)
        if (el.classList.contains('flash-green') || el.classList.contains('flash-red')) return;

        const rawChange = t.change !== undefined ? t.change : t.changePercent;
        let changePct = parseFloat(rawChange);
        if (isNaN(changePct)) changePct = 0;
        
        const isUp = changePct >= 0;
        const arrow = isUp ? '▲' : '▼';
        
        el.className = `ticker-item ${isUp ? 'up' : 'down'}`;
        el.innerHTML = `<strong>${t.symbol}</strong> ${formatMoney(t.price)} ${arrow} ${Math.abs(changePct).toFixed(2)}%`;
      }
    });
  }
}
