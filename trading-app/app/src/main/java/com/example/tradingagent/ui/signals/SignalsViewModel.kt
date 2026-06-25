package com.example.tradingagent.ui.signals

import androidx.lifecycle.ViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class Signal(
    val symbol: String,
    val price: Double,
    val changePct: Double,
    val trendScore: Double,
    val signal: String,     // "BUY", "SELL", "HOLD"
    val aiDecision: String, // "BUY", "SELL", "HOLD", "SKIP"
)

data class SignalsUiState(
    val signals: List<Signal> = emptyList(),
    val isLoading: Boolean = false,
)

class SignalsViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(
        SignalsUiState(
            signals = listOf(
                Signal(
                    symbol = "RELIANCE",
                    price = 2523.75,
                    changePct = 1.74,
                    trendScore = 0.72,
                    signal = "BUY",
                    aiDecision = "BUY",
                ),
                Signal(
                    symbol = "TCS",
                    price = 3612.30,
                    changePct = -1.03,
                    trendScore = -0.61,
                    signal = "SELL",
                    aiDecision = "SELL",
                ),
                Signal(
                    symbol = "INFY",
                    price = 1548.90,
                    changePct = 1.90,
                    trendScore = 0.48,
                    signal = "HOLD",
                    aiDecision = "HOLD",
                ),
                Signal(
                    symbol = "HDFCBANK",
                    price = 1687.40,
                    changePct = 0.32,
                    trendScore = 0.15,
                    signal = "HOLD",
                    aiDecision = "SKIP",
                ),
                Signal(
                    symbol = "ICICIBANK",
                    price = 1234.55,
                    changePct = -0.58,
                    trendScore = -0.45,
                    signal = "HOLD",
                    aiDecision = "HOLD",
                ),
                Signal(
                    symbol = "WIPRO",
                    price = 456.80,
                    changePct = 2.15,
                    trendScore = 0.85,
                    signal = "BUY",
                    aiDecision = "BUY",
                ),
            )
        )
    )
    val uiState: StateFlow<SignalsUiState> = _uiState.asStateFlow()

    fun refresh() {
        _uiState.value = _uiState.value.copy(isLoading = true)
        _uiState.value = _uiState.value.copy(isLoading = false)
    }
}
