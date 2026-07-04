package com.example.alphatrader.ui.viewmodels

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.alphatrader.data.network.PortfolioResponse
import com.example.alphatrader.data.network.RetrofitClient
import com.example.alphatrader.data.network.SignalResponse
import com.example.alphatrader.data.network.AnalyticsResponse
import com.example.alphatrader.data.network.NavHistoryItem
import com.example.alphatrader.data.network.StockDetailsResponse
import com.example.alphatrader.ui.components.AgentStatus
import com.example.alphatrader.ui.components.MarketRegion
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

import com.example.alphatrader.ui.components.TickerItem
import com.example.alphatrader.ui.components.SignalAction

data class ExecutionHistoryItem(
    val timestamp: String,
    val ticker: String,
    val action: SignalAction,
    val entry: Double,
    val exit: Double,
    val pnl: Double
)

data class DecisionLogItem(
    val icon: String,
    val title: String,
    val subtitle: String,
    val timestamp: String
)

data class DashboardState(
    val isLoading: Boolean = true,
    val marketRegion: MarketRegion = MarketRegion.US,
    val agentStatus: AgentStatus = AgentStatus.LIVE,
    val portfolio: PortfolioResponse? = null,
    val analytics: AnalyticsResponse? = null,
    val navHistory: List<NavHistoryItem> = emptyList(),
    val signals: List<SignalResponse> = emptyList(),
    val tickers: List<TickerItem> = emptyList(),
    val executionLogs: List<ExecutionHistoryItem> = emptyList(),
    val decisionLogs: List<DecisionLogItem> = emptyList(),
    val errorMessage: String? = null,
    val selectedStockSymbol: String? = null,
    val stockDetails: StockDetailsResponse? = null,
    val isStockDetailsLoading: Boolean = false
)

class DashboardViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(DashboardState())
    val uiState: StateFlow<DashboardState> = _uiState.asStateFlow()

    init {
        // Load initial data (US default)
        fetchDashboardData(MarketRegion.US)
    }

    fun toggleMarket() {
        val newMarket = if (_uiState.value.marketRegion == MarketRegion.US) MarketRegion.IN else MarketRegion.US
        _uiState.value = _uiState.value.copy(marketRegion = newMarket, isLoading = true)
        fetchDashboardData(newMarket)
    }

    private fun fetchDashboardData(market: MarketRegion) {
        viewModelScope.launch {
            try {
                val api = RetrofitClient.getInstance(if (market == MarketRegion.US) "US" else "IN")
                
                val portfolio = api.getPortfolio()
                val signalsNet = api.getSignals()
                val tradesNet = api.getTrades()
                val logsNet = api.getLogs()
                val tickersNet = api.getTickers()
                
                val analyticsNet = try { api.getAnalytics() } catch (e: Exception) { null }
                val navHistoryNet = try { api.getNavHistory().data } catch (e: Exception) { emptyList() }
                
                val status = if (portfolio.agentStatus == "running") AgentStatus.LIVE else AgentStatus.SLEEPING
                
                val mappedTickers = tickersNet.ticker.map {
                    TickerItem(it.symbol, it.price, it.change_pct)
                }
                
                val mappedExecutionLogs = tradesNet.map {
                    val actionEnum = when(it.action.uppercase()) {
                        "BUY" -> SignalAction.BUY
                        "SELL" -> SignalAction.SELL
                        else -> SignalAction.HOLD
                    }
                    val parsedPnl = it.pnl?.toDoubleOrNull() ?: 0.0
                    val qty = it.quantity ?: 1.0
                    val computedEntry = if (actionEnum == SignalAction.SELL) it.price - (parsedPnl / qty) else it.price
                    val computedExit = if (actionEnum == SignalAction.BUY) it.price + (parsedPnl / qty) else it.price
                    ExecutionHistoryItem(
                        timestamp = it.time,
                        ticker = it.symbol,
                        action = actionEnum,
                        entry = computedEntry,
                        exit = computedExit,
                        pnl = parsedPnl
                    )
                }
                
                val mappedDecisionLogs = logsNet.mapNotNull { logString ->
                    if (logString.contains("| ERROR |") || logString.contains("| CRITICAL |") || logString.contains("ERROR") || logString.contains("CRITICAL")) {
                        val lowerLog = logString.lowercase()
                        val isIgnorable = listOf("timeout", "retry", "connection", "network").any { lowerLog.contains(it) }
                        
                        // Try to extract timestamp if it starts with one, else leave blank
                        val timestampRegex = Regex("^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}")
                        val match = timestampRegex.find(logString)
                        val timestamp = match?.value ?: ""
                        
                        DecisionLogItem(
                            icon = if (isIgnorable) "⚠️" else "🚨",
                            title = if (isIgnorable) "Ignorable Error" else "Severe Error",
                            subtitle = logString,
                            timestamp = timestamp
                        )
                    } else {
                        null
                    }
                }.takeLast(20).reversed()
                
                _uiState.value = _uiState.value.copy(
                    isLoading = false,
                    portfolio = portfolio,
                    analytics = analyticsNet,
                    navHistory = navHistoryNet,
                    signals = signalsNet,
                    tickers = mappedTickers,
                    decisionLogs = mappedDecisionLogs,
                    executionLogs = mappedExecutionLogs,
                    agentStatus = status
                )
            } catch (e: Exception) {
                _uiState.value = _uiState.value.copy(
                    isLoading = false,
                    errorMessage = "Failed to connect to EC2 server: ${e.message}"
                )
            }
        }
    }

    fun openStockDetails(symbol: String) {
        val cleanSymbol = symbol.removeSuffix(".NS").removeSuffix(".L")
        _uiState.value = _uiState.value.copy(
            selectedStockSymbol = cleanSymbol,
            isStockDetailsLoading = true,
            stockDetails = null
        )
        viewModelScope.launch {
            try {
                val api = RetrofitClient.getInstance(if (_uiState.value.marketRegion == MarketRegion.US) "US" else "IN")
                val details = api.getStockDetails(cleanSymbol)
                _uiState.value = _uiState.value.copy(
                    isStockDetailsLoading = false,
                    stockDetails = details
                )
            } catch (e: Exception) {
                _uiState.value = _uiState.value.copy(
                    isStockDetailsLoading = false,
                    errorMessage = "Failed to load details for $symbol"
                )
            }
        }
    }

    fun closeStockDetails() {
        _uiState.value = _uiState.value.copy(
            selectedStockSymbol = null,
            stockDetails = null
        )
    }
}
