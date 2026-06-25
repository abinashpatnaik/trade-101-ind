package com.example.tradingagent.data.repository

import com.example.tradingagent.data.api.AppsHealth
import com.example.tradingagent.data.api.MarketConfig
import com.example.tradingagent.data.api.PortfolioResponse
import com.example.tradingagent.data.api.Position
import com.example.tradingagent.data.api.Signal
import com.example.tradingagent.data.api.TickerItem
import com.example.tradingagent.data.api.Trade
import com.example.tradingagent.data.api.TradingApiService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Single source of truth for all trading data.
 *
 * Wraps [TradingApiService] and exposes every data type as a [StateFlow].
 * Two background loops keep the data fresh:
 *  - **Fast loop** (default 5 s): portfolio, positions, signals, ticker.
 *  - **Slow loop** (default 30 s): trades today, all trades, health, config.
 *
 * Call [startAutoRefresh] to begin and [stopAutoRefresh] to cancel.
 * A one-shot [refresh] is also available for pull-to-refresh UIs.
 */
class TradingRepository(
    private val apiService: TradingApiService,
    private val fastIntervalMs: Long = 5_000L,
    private val slowIntervalMs: Long = 30_000L
) {

    // ── Coroutine scope used for background refresh loops ────────────
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var fastJob: Job? = null
    private var slowJob: Job? = null

    // ── Portfolio ────────────────────────────────────────────────────
    private val _portfolio = MutableStateFlow(PortfolioResponse())
    val portfolio: StateFlow<PortfolioResponse> = _portfolio.asStateFlow()

    // ── Positions ────────────────────────────────────────────────────
    private val _positions = MutableStateFlow<List<Position>>(emptyList())
    val positions: StateFlow<List<Position>> = _positions.asStateFlow()

    // ── Signals ──────────────────────────────────────────────────────
    private val _signals = MutableStateFlow<List<Signal>>(emptyList())
    val signals: StateFlow<List<Signal>> = _signals.asStateFlow()

    // ── Ticker ───────────────────────────────────────────────────────
    private val _ticker = MutableStateFlow<List<TickerItem>>(emptyList())
    val ticker: StateFlow<List<TickerItem>> = _ticker.asStateFlow()

    // ── Trades Today ─────────────────────────────────────────────────
    private val _tradesToday = MutableStateFlow<List<Trade>>(emptyList())
    val tradesToday: StateFlow<List<Trade>> = _tradesToday.asStateFlow()

    // ── All Trades ───────────────────────────────────────────────────
    private val _allTrades = MutableStateFlow<List<Trade>>(emptyList())
    val allTrades: StateFlow<List<Trade>> = _allTrades.asStateFlow()

    // ── Apps Health ──────────────────────────────────────────────────
    private val _appsHealth = MutableStateFlow(AppsHealth())
    val appsHealth: StateFlow<AppsHealth> = _appsHealth.asStateFlow()

    // ── Market Config ────────────────────────────────────────────────
    private val _marketConfig = MutableStateFlow(MarketConfig())
    val marketConfig: StateFlow<MarketConfig> = _marketConfig.asStateFlow()

    // ── Loading / error state ────────────────────────────────────────
    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _lastError = MutableStateFlow<String?>(null)
    val lastError: StateFlow<String?> = _lastError.asStateFlow()

    // ── One-shot refresh (for pull-to-refresh) ───────────────────────

    /** Fetch *everything* once. Safe to call from any coroutine context. */
    suspend fun refresh() {
        _isLoading.value = true
        _lastError.value = null
        try {
            refreshFast()
            refreshSlow()
        } catch (e: Exception) {
            _lastError.value = e.message
        } finally {
            _isLoading.value = false
        }
    }

    // ── Auto-refresh lifecycle ───────────────────────────────────────

    /** Start two background loops. Calling again is a no-op. */
    fun startAutoRefresh() {
        if (fastJob?.isActive == true) return

        fastJob = scope.launch {
            while (true) {
                try {
                    refreshFast()
                } catch (_: Exception) { /* swallow – will retry next tick */ }
                delay(fastIntervalMs)
            }
        }

        slowJob = scope.launch {
            while (true) {
                try {
                    refreshSlow()
                } catch (_: Exception) { }
                delay(slowIntervalMs)
            }
        }
    }

    /** Cancel the background loops. */
    fun stopAutoRefresh() {
        fastJob?.cancel()
        slowJob?.cancel()
        fastJob = null
        slowJob = null
    }

    // ── Update intervals at runtime ──────────────────────────────────

    /** Restart auto-refresh with new intervals. */
    fun restartAutoRefresh(
        newFastMs: Long = fastIntervalMs,
        newSlowMs: Long = slowIntervalMs
    ) {
        stopAutoRefresh()
        // Repository is immutable in interval fields, so create new jobs
        // using the provided values directly.
        fastJob = scope.launch {
            while (true) {
                try { refreshFast() } catch (_: Exception) { }
                delay(newFastMs)
            }
        }
        slowJob = scope.launch {
            while (true) {
                try { refreshSlow() } catch (_: Exception) { }
                delay(newSlowMs)
            }
        }
    }

    // ── Internal fetchers ────────────────────────────────────────────

    /** Fast-changing data: portfolio, positions, signals, ticker. */
    private suspend fun refreshFast() {
        apiService.getPortfolio()?.let { _portfolio.value = it }
        _positions.value = apiService.getPositions()
        _signals.value = apiService.getSignals()
        _ticker.value = apiService.getTicker()
    }

    /** Slow-changing data: trades, health, config. */
    private suspend fun refreshSlow() {
        _tradesToday.value = apiService.getTradesToday()
        _allTrades.value = apiService.getAllTrades()
        apiService.getAppsHealth()?.let { _appsHealth.value = it }
        apiService.getMarketConfig()?.let { _marketConfig.value = it }
    }
}
