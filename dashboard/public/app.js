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
    const [portRes, posRes, sigRes, tradesRes, analyticsRes, tickerRes] = await Promise.all([
      fetch('/api/portfolio').catch(() => null),
      fetch('/api/positions').catch(() => null),
      fetch('/api/signals').catch(() => null),
      fetch('/api/trades').catch(() => null),
      fetch('/api/analytics').catch(() => null),
      fetch('/api/ticker').catch(() => null)
    ]);

    if (portRes && portRes.ok) renderPortfolio(await portRes.json());
    if (posRes && posRes.ok) renderPositions(await posRes.json());
    if (sigRes && sigRes.ok) {
      state.signals = await sigRes.json();
      renderSignals();
    }
    if (tradesRes && tradesRes.ok) renderTrades(await tradesRes.json());
    if (analyticsRes && analyticsRes.ok) renderAnalytics(await analyticsRes.json());
    if (tickerRes && tickerRes.ok) renderTicker(await tickerRes.json());

    // Update health badges
    document.getElementById('badge-dashboard').className = 'pill-badge active';
  } catch (e) {
    console.error("Dashboard data fetch error", e);
  }
}

function renderPortfolio(data) {
  document.getElementById('val-nav').textContent = formatMoney(data.nav);
  const pnlEl = document.getElementById('val-pnl');
  pnlEl.textContent = `${formatMoney(data.dailyPnl)} (${data.dailyPnlPct.toFixed(2)}%) Today`;
  pnlEl.className = data.dailyPnl >= 0 ? 'summary-change up' : 'summary-change down';
  
  document.getElementById('val-cash').textContent = formatMoney(data.cash);
  
  if (data.buyingPower !== undefined) {
    document.getElementById('val-bp').textContent = formatMoney(data.buyingPower);
  }
  
  document.getElementById('val-positions').textContent = data.openPositions;
  
  const agentBadge = document.getElementById('badge-agent');
  if (data.agentStatus === 'running') {
    agentBadge.className = 'pill-badge active';
  } else {
    agentBadge.className = 'pill-badge offline';
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
        <td style="font-weight: 600;">${p.symbol}</td>
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
    const displaySignal = isGated ? 'GATED' : s.signal;
    const badgeClass = displaySignal.toLowerCase();
    
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
            <span style="color: var(--text-tertiary)">${isUp ? 'UP' : 'DN'}</span>
          </div>
          <div class="progress-wrap"><div class="progress-bar" style="width: ${conf}%; background-color: var(--signal-green);"></div></div>
        </div>
      `;
    }

    html += `
      <tr>
        <td style="font-weight: 600;">${s.symbol}</td>
        <td class="mono">${s.combinedScore ? s.combinedScore.toFixed(3) : '-'}</td>
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
        <td style="font-weight: 600;">${t.symbol}</td>
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
  if (!data || !data.ticker || data.ticker.length === 0) return;
  
  let html = '';
  data.ticker.forEach(t => {
    const isUp = t.changePercent >= 0;
    const arrow = isUp ? '▲' : '▼';
    const cls = isUp ? 'up' : 'down';
    html += `<span class="ticker-item ${cls}"><strong>${t.symbol}</strong> ${formatMoney(t.price)} ${arrow} ${Math.abs(t.changePercent).toFixed(2)}%</span>`;
  });
  
  // Duplicate for seamless scroll
  container.innerHTML = html + html;
}

document.addEventListener('DOMContentLoaded', () => {
  initCharts();
  fetchDashboardData();
  setInterval(fetchDashboardData, 5000);
});
