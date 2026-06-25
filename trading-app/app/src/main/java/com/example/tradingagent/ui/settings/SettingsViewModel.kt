package com.example.tradingagent.ui.settings

import androidx.lifecycle.ViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class ThemeMode {
    SYSTEM,
    LIGHT,
    DARK,
}

data class SettingsUiState(
    val serverUrl: String = "http://192.168.1.100:8000",
    val username: String = "admin",
    val password: String = "password123",
    val themeMode: ThemeMode = ThemeMode.SYSTEM,
    val connectionStatus: ConnectionStatus = ConnectionStatus.IDLE,
    val appVersion: String = "1.0.0",
)

enum class ConnectionStatus {
    IDLE,
    TESTING,
    SUCCESS,
    FAILURE,
}

class SettingsViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(SettingsUiState())
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    fun updateServerUrl(url: String) {
        _uiState.value = _uiState.value.copy(serverUrl = url, connectionStatus = ConnectionStatus.IDLE)
    }

    fun updateUsername(username: String) {
        _uiState.value = _uiState.value.copy(username = username)
    }

    fun updatePassword(password: String) {
        _uiState.value = _uiState.value.copy(password = password)
    }

    fun setThemeMode(mode: ThemeMode) {
        _uiState.value = _uiState.value.copy(themeMode = mode)
    }

    fun testConnection() {
        // Will be wired to repository later
        _uiState.value = _uiState.value.copy(connectionStatus = ConnectionStatus.TESTING)
        // Simulate result
        _uiState.value = _uiState.value.copy(connectionStatus = ConnectionStatus.SUCCESS)
    }
}
