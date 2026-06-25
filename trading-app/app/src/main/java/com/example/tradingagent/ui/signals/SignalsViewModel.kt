package com.example.tradingagent.ui.signals

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.tradingagent.TradingAgentApp
import com.example.tradingagent.data.api.Signal as ApiSignal
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** UI-friendly signal model. */
data class Signal(
    val symbol: String,
    val price: Double,
    val changePct: Double,
    val trendScore: Double,
    val signal: String,     // "BUY", "SELL", "HOLD"
    val aiDecision: String, // "APPROVED", "REJECTED", "IDLE", "OFF"
)

data class SignalsUiState(
    val signals: List<Signal> = emptyList(),
    val isLoading: Boolean = true,
)

class SignalsViewModel : ViewModel() {
    private val repository = TradingAgentApp.instance.repository
    private val _uiState = MutableStateFlow(SignalsUiState())
    val uiState: StateFlow<SignalsUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            repository.signals.collect { apiSignals ->
                val mapped = apiSignals.map { it.toUiSignal() }
                _uiState.value = SignalsUiState(
                    signals = mapped,
                    isLoading = false,
                )
            }
        }
    }

    fun refresh() {
        viewModelScope.launch { repository.refresh() }
    }

    private fun ApiSignal.toUiSignal(): Signal = Signal(
        symbol = symbol,
        price = price ?: 0.0,
        changePct = changePct ?: 0.0,
        trendScore = trendScore ?: combinedScore ?: 0.0,
        signal = signal ?: "HOLD",
        aiDecision = aiDecision ?: "OFF",
    )
}
