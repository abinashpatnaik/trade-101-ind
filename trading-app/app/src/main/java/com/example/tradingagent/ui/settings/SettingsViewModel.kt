package com.example.tradingagent.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.tradingagent.TradingAgentApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class ThemeMode {
    SYSTEM,
    LIGHT,
    DARK,
}

data class SettingsUiState(
    val serverUrl: String = "",
    val username: String = "",
    val password: String = "",
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
    private val app = TradingAgentApp.instance
    private val settings = app.settingsManager

    private val _uiState = MutableStateFlow(
        SettingsUiState(
            serverUrl = settings.serverUrl,
            username = settings.username ?: "",
            password = settings.password ?: "",
            themeMode = when (settings.darkMode) {
                true -> ThemeMode.DARK
                false -> ThemeMode.LIGHT
                null -> ThemeMode.SYSTEM
            },
        )
    )
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    fun updateServerUrl(url: String) {
        _uiState.value = _uiState.value.copy(serverUrl = url, connectionStatus = ConnectionStatus.IDLE)
        settings.serverUrl = url
        app.reconfigureApi()
    }

    fun updateUsername(username: String) {
        _uiState.value = _uiState.value.copy(username = username)
        settings.username = username.ifBlank { null }
        app.reconfigureApi()
    }

    fun updatePassword(password: String) {
        _uiState.value = _uiState.value.copy(password = password)
        settings.password = password.ifBlank { null }
        app.reconfigureApi()
    }

    fun setThemeMode(mode: ThemeMode) {
        _uiState.value = _uiState.value.copy(themeMode = mode)
        settings.darkMode = when (mode) {
            ThemeMode.DARK -> true
            ThemeMode.LIGHT -> false
            ThemeMode.SYSTEM -> null
        }
    }

    fun testConnection() {
        _uiState.value = _uiState.value.copy(connectionStatus = ConnectionStatus.TESTING)
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                try {
                    val portfolio = app.apiService.getPortfolio()
                    portfolio != null
                } catch (_: Exception) {
                    false
                }
            }
            _uiState.value = _uiState.value.copy(
                connectionStatus = if (result) ConnectionStatus.SUCCESS else ConnectionStatus.FAILURE,
            )
        }
    }
}
