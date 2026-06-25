package com.example.tradingagent.ui.home

import androidx.lifecycle.ViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class HomeUiState(
    val nav: Double = 99573.46,
    val cash: Double = 85000.0,
    val dailyPnl: Double = 127.50,
    val dailyPnlPct: Double = 0.13,
    val openPositions: Int = 3,
    val winRate: Double = 66.7,
    val agentStatus: String = "running",
    val marketOpen: Boolean = false,
    val tradesToday: Int = 5,
    val isLoading: Boolean = false,
)

class HomeViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(HomeUiState())
    val uiState: StateFlow<HomeUiState> = _uiState.asStateFlow()

    fun refresh() {
        // Will be wired to repository later
        _uiState.value = _uiState.value.copy(isLoading = true)
        // Simulate refresh completing
        _uiState.value = _uiState.value.copy(isLoading = false)
    }
}
