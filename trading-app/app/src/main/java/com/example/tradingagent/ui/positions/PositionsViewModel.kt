package com.example.tradingagent.ui.positions

import androidx.lifecycle.ViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class Position(
    val symbol: String,
    val quantity: Int,
    val avgCost: Double,
    val currentPrice: Double,
    val exchange: String = "NSE",
) {
    val unrealizedPnl: Double
        get() = (currentPrice - avgCost) * quantity
    val unrealizedPnlPct: Double
        get() = if (avgCost > 0) ((currentPrice - avgCost) / avgCost) * 100.0 else 0.0
    val marketValue: Double
        get() = currentPrice * quantity
}

data class PositionsUiState(
    val positions: List<Position> = emptyList(),
    val isLoading: Boolean = false,
)

class PositionsViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(
        PositionsUiState(
            positions = listOf(
                Position(
                    symbol = "RELIANCE",
                    quantity = 10,
                    avgCost = 2480.50,
                    currentPrice = 2523.75,
                    exchange = "NSE",
                ),
                Position(
                    symbol = "TCS",
                    quantity = 5,
                    avgCost = 3650.00,
                    currentPrice = 3612.30,
                    exchange = "NSE",
                ),
                Position(
                    symbol = "INFY",
                    quantity = 15,
                    avgCost = 1520.00,
                    currentPrice = 1548.90,
                    exchange = "NSE",
                ),
            )
        )
    )
    val uiState: StateFlow<PositionsUiState> = _uiState.asStateFlow()

    fun refresh() {
        _uiState.value = _uiState.value.copy(isLoading = true)
        _uiState.value = _uiState.value.copy(isLoading = false)
    }
}
