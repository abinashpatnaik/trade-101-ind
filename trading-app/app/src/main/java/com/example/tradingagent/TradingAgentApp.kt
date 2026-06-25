package com.example.tradingagent

import android.app.Application
import com.example.tradingagent.data.SettingsManager
import com.example.tradingagent.data.api.TradingApiService
import com.example.tradingagent.data.repository.TradingRepository

/**
 * Application-level singleton container for shared services.
 */
class TradingAgentApp : Application() {

    lateinit var settingsManager: SettingsManager
        private set
    lateinit var apiService: TradingApiService
        private set
    lateinit var repository: TradingRepository
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this

        settingsManager = SettingsManager(this)
        apiService = TradingApiService(
            baseUrl = settingsManager.serverUrl,
            username = settingsManager.username,
            password = settingsManager.password,
        )
        repository = TradingRepository(apiService)
        repository.startAutoRefresh()
    }

    /** Reconfigure the API service when settings change. */
    fun reconfigureApi() {
        apiService.updateBaseUrl(settingsManager.serverUrl)
        apiService.updateCredentials(settingsManager.username, settingsManager.password)
    }

    companion object {
        lateinit var instance: TradingAgentApp
            private set
    }
}
