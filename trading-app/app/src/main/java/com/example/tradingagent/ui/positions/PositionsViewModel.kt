package com.example.tradingagent.ui.positions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.tradingagent.TradingAgentApp
import com.example.tradingagent.data.api.Position as ApiPosition
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** UI-friendly position model with computed P&L. */
data class Position(
    val symbol: String,
    val quantity: Int,
    val avgCost: Double,
    val currentPrice: Double,
    val exchange: String = "US",
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
    val isLoading: Boolean = true,
)

class PositionsViewModel : ViewModel() {
    private val repository = TradingAgentApp.instance.repository
    private val _uiState = MutableStateFlow(PositionsUiState())
    val uiState: StateFlow<PositionsUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            repository.positions.collect { apiPositions ->
                val mapped = apiPositions.map { it.toUiPosition() }
                _uiState.value = PositionsUiState(
                    positions = mapped,
                    isLoading = false,
                )
            }
        }
    }

    fun refresh() {
        viewModelScope.launch { repository.refresh() }
    }

    /** Map API Position (supports both IBKR and Alpaca formats) to UI Position. */
    private fun ApiPosition.toUiPosition(): Position {
        return Position(
            symbol = displaySymbol,
            quantity = displayQuantity.toInt(),
            avgCost = displayAvgCost,
            currentPrice = displayCurrentPrice,
            exchange = "US",
        )
    }
}
