package com.example.tradingagent.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.tradingagent.TradingAgentApp
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class HomeUiState(
    val nav: Double = 0.0,
    val cash: Double = 0.0,
    val dailyPnl: Double = 0.0,
    val dailyPnlPct: Double = 0.0,
    val openPositions: Int = 0,
    val winRate: Double = 0.0,
    val agentStatus: String = "offline",
    val marketOpen: Boolean = false,
    val tradesToday: Int = 0,
    val isLoading: Boolean = true,
)

class HomeViewModel : ViewModel() {
    private val repository = TradingAgentApp.instance.repository
    private val _uiState = MutableStateFlow(HomeUiState())
    val uiState: StateFlow<HomeUiState> = _uiState.asStateFlow()

    init {
        // Observe repository portfolio changes
        viewModelScope.launch {
            repository.portfolio.collect { p ->
                _uiState.value = _uiState.value.copy(
                    nav = p.nav,
                    cash = p.cash,
                    dailyPnl = p.dailyPnl,
                    dailyPnlPct = p.dailyPnlPct,
                    openPositions = p.openPositions,
                    winRate = p.winRate,
                    agentStatus = p.agentStatus,
                    marketOpen = p.marketOpen,
                    tradesToday = p.tradesToday,
                    isLoading = false,
                )
            }
        }
        viewModelScope.launch {
            repository.isLoading.collect { loading ->
                _uiState.value = _uiState.value.copy(isLoading = loading)
            }
        }
    }

    fun refresh() {
        viewModelScope.launch {
            repository.refresh()
        }
    }
}
