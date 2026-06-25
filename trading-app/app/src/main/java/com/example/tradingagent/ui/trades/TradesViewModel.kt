package com.example.tradingagent.ui.trades

import androidx.lifecycle.ViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class Trade(
    val id: String,
    val date: String,       // "2026-06-25"
    val time: String,       // "09:32:15"
    val action: String,     // "BUY" or "SELL"
    val symbol: String,
    val quantity: Int,
    val price: Double,
    val pnl: Double?,       // null for open BUYs, value for closed trades
    val mode: String,       // "PAPER" or "LIVE"
)

data class TradesUiState(
    val trades: List<Trade> = emptyList(),
    val selectedFilter: TradeFilter = TradeFilter.TODAY,
    val isLoading: Boolean = false,
)

enum class TradeFilter {
    TODAY,
    ALL_HISTORY,
}

class TradesViewModel : ViewModel() {
    private val allTrades = listOf(
        // Today's trades
        Trade(
            id = "1",
            date = "2026-06-25",
            time = "09:32:15",
            action = "BUY",
            symbol = "RELIANCE",
            quantity = 10,
            price = 2480.50,
            pnl = null,
            mode = "PAPER",
        ),
        Trade(
            id = "2",
            date = "2026-06-25",
            time = "10:15:42",
            action = "SELL",
            symbol = "HDFCBANK",
            quantity = 8,
            price = 1695.20,
            pnl = 312.00,
            mode = "PAPER",
        ),
        Trade(
            id = "3",
            date = "2026-06-25",
            time = "11:05:33",
            action = "BUY",
            symbol = "INFY",
            quantity = 15,
            price = 1520.00,
            pnl = null,
            mode = "PAPER",
        ),
        Trade(
            id = "4",
            date = "2026-06-25",
            time = "13:22:08",
            action = "SELL",
            symbol = "WIPRO",
            quantity = 20,
            price = 462.30,
            pnl = -185.00,
            mode = "PAPER",
        ),
        Trade(
            id = "5",
            date = "2026-06-25",
            time = "14:45:17",
            action = "BUY",
            symbol = "TCS",
            quantity = 5,
            price = 3650.00,
            pnl = null,
            mode = "PAPER",
        ),
        // Historical trades
        Trade(
            id = "6",
            date = "2026-06-24",
            time = "09:45:00",
            action = "BUY",
            symbol = "RELIANCE",
            quantity = 5,
            price = 2450.00,
            pnl = null,
            mode = "PAPER",
        ),
        Trade(
            id = "7",
            date = "2026-06-24",
            time = "14:30:00",
            action = "SELL",
            symbol = "RELIANCE",
            quantity = 5,
            price = 2490.00,
            pnl = 200.00,
            mode = "PAPER",
        ),
        Trade(
            id = "8",
            date = "2026-06-23",
            time = "10:00:00",
            action = "BUY",
            symbol = "ICICIBANK",
            quantity = 12,
            price = 1210.00,
            pnl = null,
            mode = "LIVE",
        ),
        Trade(
            id = "9",
            date = "2026-06-23",
            time = "15:10:00",
            action = "SELL",
            symbol = "ICICIBANK",
            quantity = 12,
            price = 1240.50,
            pnl = 366.00,
            mode = "LIVE",
        ),
    )

    private val _uiState = MutableStateFlow(
        TradesUiState(
            trades = allTrades.filter { it.date == "2026-06-25" },
        )
    )
    val uiState: StateFlow<TradesUiState> = _uiState.asStateFlow()

    fun setFilter(filter: TradeFilter) {
        val filtered = when (filter) {
            TradeFilter.TODAY -> allTrades.filter { it.date == "2026-06-25" }
            TradeFilter.ALL_HISTORY -> allTrades
        }
        _uiState.value = _uiState.value.copy(
            selectedFilter = filter,
            trades = filtered,
        )
    }

    fun refresh() {
        _uiState.value = _uiState.value.copy(isLoading = true)
        _uiState.value = _uiState.value.copy(isLoading = false)
    }
}
