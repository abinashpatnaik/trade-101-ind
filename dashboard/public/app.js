const formatMoney = (val) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(val);
const formatPct = (val) => (val > 0 ? '+' : '') + parseFloat(val).toFixed(2) + '%';
const formatNum = (val) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(val);

let state = {
  navHistory: [],
  signals: [],
  sigPage: 1,
  sigFilter: 'ALL', // ALL, BUY, SELL, HOLD, GATED
};

async function fetchDashboardData() {
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
    
    if (portRes && portRes.ok) renderPortfolio(await portRes.json(), healthData);
    if (posRes && posRes.ok) renderPositions(await posRes.json());
    if (sigRes && sigRes.ok) {
      state.signals = await sigRes.json();
      renderSignals();
    }
    if (tradesRes && tradesRes.ok) renderTrades(await tradesRes.json());
    if (analyticsRes && analyticsRes.ok) renderAnalytics(await analyticsRes.json());
    if (tickerRes && tickerRes.ok) renderTicker(await tickerRes.json());

  } catch (e) {
    console.error("Dashboard data fetch error", e);
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
      marketBadge.textContent = 'Market Closed';
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
          <div style="font-size: 11px; color: var(--text-secondary);">TP: ${p.takeProfit || '-'}</div>
          <div style="font-size: 11px; color: var(--text-secondary);">SL: ${p.stopLoss || '-'}</div>
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
    const isGated = s.signal === 'HOLD' && s.holdReason && s.combinedScore >= (s.buyThreshold || 0.48);
    let displaySignal = isGated ? 'GATED' : s.signal;
    if (isGated && s.holdReason) {
      displaySignal += ` (${s.holdReason})`;
    }
    const badgeClass = isGated ? 'gated' : s.signal.toLowerCase();
    
    // Engine Score Color
    let scoreClass = '';
    if (s.combinedScore > 0.1) scoreClass = 'status-buy';
    else if (s.combinedScore < -0.1) scoreClass = 'status-sell';
    
    // ML Confidence progress bar
    let mlHtml = '-';
    if (s.mlConfidence) {
      const conf = s.mlConfidence * 100;
      const isUp = conf >= 50;
      const color = isUp ? 'var(--signal-green)' : 'var(--signal-red)';
      const displayConf = isUp ? conf : 100 - conf;
      mlHtml = `
        <div style="width: 80px;">
          <div style="display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 2px;">
            <span style="color: ${color}; font-weight: 600;">${displayConf.toFixed(0)}%</span>
            <span style="color: ${color};">${isUp ? '▲' : '▼'}</span>
          </div>
          <div class="progress-wrap"><div class="progress-bar" style="width: ${displayConf}%; background-color: ${color};"></div></div>
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
    html += `
      <tr>
        <td style="color: var(--text-secondary); font-size: 11px;">${t.time}</td>
        <td style="font-weight: 600;"><span class="clickable-symbol" onclick="showStockDetails('${t.symbol}')">${t.symbol}</span></td>
        <td><span class="${actionClass}">${t.action}</span></td>
        <td class="mono td-right">${formatMoney(t.price)}</td>
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
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false, color: '#1F1F2E' } },
          y: { grid: { color: '#1F1F2E' } }
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

  // Load initial NAV history
  fetchNavHistory('1mo');
}

async function fetchNavHistory(range) {
  try {
    const res = await fetch(`/api/nav-history?range=${range}`);
    if (res.ok && charts.nav) {
      const data = await res.json();
      charts.nav.data.labels = data.map(d => d.date);
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

function renderTicker(data) {
  const container = document.getElementById('ticker-content');
  if (!data || !data.ticker || data.ticker.length === 0) {
    container.innerHTML = '<span class="ticker-item">No ticker data available</span>';
    return;
  }
  
  let html = '';
  data.ticker.forEach(t => {
    // The Python backend writes 'change', but SPY/QQQ might use 'changePercent'
    const rawChange = t.change !== undefined ? t.change : t.changePercent;
    let changePct = parseFloat(rawChange);
    if (isNaN(changePct)) changePct = 0;
    
    const isUp = changePct >= 0;
    const arrow = isUp ? '▲' : '▼';
    const cls = isUp ? 'up' : 'down';
    html += `<span class="ticker-item ${cls}"><strong>${t.symbol}</strong> ${formatMoney(t.price)} ${arrow} ${Math.abs(changePct).toFixed(2)}%</span>`;
  });
  
  // Duplicate for seamless scroll
  container.innerHTML = html + html;
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
            x: { grid: { display: false, color: '#1F1F2E' } },
            y: { grid: { color: '#1F1F2E' } }
          }
        }
      });
    }
    
    document.getElementById('stockModal').classList.add('open');
  } catch (e) {
    console.error("Failed to load stock details", e);
  }
}


document.addEventListener('DOMContentLoaded', () => {
  initCharts();
  fetchDashboardData();
  setInterval(fetchDashboardData, 5000);
});
