package com.example.tradingagent.ui.trades

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.tradingagent.TradingAgentApp
import com.example.tradingagent.data.api.Trade as ApiTrade
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** UI-friendly trade model. */
data class Trade(
    val id: String,
    val date: String,       // "2026-06-25"
    val time: String,       // "09:32:15"
    val action: String,     // "BUY" or "SELL"
    val symbol: String,
    val quantity: Int,
    val price: Double,
    val pnl: Double?,       // null for open BUYs
    val mode: String,       // "PAPER" or "LIVE"
)

data class TradesUiState(
    val trades: List<Trade> = emptyList(),
    val selectedFilter: TradeFilter = TradeFilter.ALL_HISTORY,
    val isLoading: Boolean = true,
)

enum class TradeFilter {
    TODAY,
    ALL_HISTORY,
}

class TradesViewModel : ViewModel() {
    private val repository = TradingAgentApp.instance.repository

    private val _uiState = MutableStateFlow(TradesUiState())
    val uiState: StateFlow<TradesUiState> = _uiState.asStateFlow()

    init {
        // Default to ALL_HISTORY so user sees trades immediately
        viewModelScope.launch {
            repository.allTrades.collect { apiTrades ->
                if (_uiState.value.selectedFilter == TradeFilter.ALL_HISTORY) {
                    _uiState.value = _uiState.value.copy(
                        trades = apiTrades.mapIndexed { i, t -> t.toUiTrade(i) },
                        isLoading = false,
                    )
                }
            }
        }
        viewModelScope.launch {
            repository.tradesToday.collect { apiTrades ->
                if (_uiState.value.selectedFilter == TradeFilter.TODAY) {
                    _uiState.value = _uiState.value.copy(
                        trades = apiTrades.mapIndexed { i, t -> t.toUiTrade(i) },
                        isLoading = false,
                    )
                }
            }
        }
    }

    fun setFilter(filter: TradeFilter) {
        _uiState.value = _uiState.value.copy(selectedFilter = filter, isLoading = true)
        viewModelScope.launch {
            val trades = when (filter) {
                TradeFilter.TODAY -> repository.tradesToday.value
                TradeFilter.ALL_HISTORY -> repository.allTrades.value
            }
            _uiState.value = _uiState.value.copy(
                trades = trades.mapIndexed { i, t -> t.toUiTrade(i) },
                isLoading = false,
            )
        }
    }

    fun refresh() {
        viewModelScope.launch { repository.refresh() }
    }

    private fun ApiTrade.toUiTrade(index: Int): Trade = Trade(
        id = "$index-$date-$symbol",
        date = date,
        time = time,
        action = action,
        symbol = symbol,
        quantity = quantity?.toIntOrNull() ?: 0,
        price = price?.toDoubleOrNull() ?: 0.0,
        pnl = pnl?.toDoubleOrNull(),
        mode = (mode ?: "paper").uppercase(),
    )
}
