package com.example.alphatrader.data

import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.util.Log
import com.example.alphatrader.data.network.RetrofitClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

class PollingService : Service() {
    private val serviceJob = Job()
    private val serviceScope = CoroutineScope(Dispatchers.IO + serviceJob)
    private lateinit var notificationHelper: NotificationHelper

    // State tracking
    private var lastUsTradeTime: String? = null
    private var lastInTradeTime: String? = null
    private var lastUsAgentStatus: String? = null
    private var lastInAgentStatus: String? = null
    private var lastUsErrorLog: String? = null
    private var lastInErrorLog: String? = null

    override fun onCreate() {
        super.onCreate()
        notificationHelper = NotificationHelper(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val notification = notificationHelper.buildForegroundNotification("Monitoring markets (US & IN)...")
        startForeground(1, notification)

        startPolling()

        return START_STICKY
    }

    private fun startPolling() {
        serviceScope.launch {
            while (isActive) {
                try {
                    pollMarket("US")
                } catch (e: Exception) {
                    Log.e("PollingService", "Error polling US market", e)
                }

                try {
                    pollMarket("IN")
                } catch (e: Exception) {
                    Log.e("PollingService", "Error polling IN market", e)
                }

                delay(30_000) // 30 seconds
            }
        }
    }

    private suspend fun pollMarket(market: String) {
        val api = RetrofitClient.getInstance(market)
        
        // 1. Check Agent Status
        val portfolio = api.getPortfolio()
        val currentStatus = portfolio.agentStatus
        
        val lastStatus = if (market == "US") lastUsAgentStatus else lastInAgentStatus
        if (lastStatus != null && lastStatus != currentStatus) {
            notificationHelper.notifyAgentStatus(market, currentStatus.uppercase())
        }
        if (market == "US") lastUsAgentStatus = currentStatus else lastInAgentStatus = currentStatus

        // 2. Check Trades
        val trades = api.getTrades()
        if (trades.isNotEmpty()) {
            val latestTrade = trades.last() // Assuming newest are at the end
            val currentTradeTime = latestTrade.date + latestTrade.time
            val lastTradeTime = if (market == "US") lastUsTradeTime else lastInTradeTime
            
            if (lastTradeTime != null && lastTradeTime != currentTradeTime) {
                notificationHelper.notifyTrade(market, latestTrade.symbol, latestTrade.action, latestTrade.price)
            }
            if (market == "US") lastUsTradeTime = currentTradeTime else lastInTradeTime = currentTradeTime
        }

        // 3. Check Server Errors
        val logs = api.getLogs()
        val latestError = logs.findLast { it.contains("ERROR") || it.contains("CRITICAL") }
        if (latestError != null) {
            val lowerLog = latestError.lowercase()
            val isIgnorable = listOf("timeout", "retry", "connection", "network").any { lowerLog.contains(it) }
            
            if (!isIgnorable) {
                val lastError = if (market == "US") lastUsErrorLog else lastInErrorLog
                if (lastError != null && lastError != latestError) {
                    notificationHelper.notifyError(market, "Critical issue detected: $latestError")
                }
                if (market == "US") lastUsErrorLog = latestError else lastInErrorLog = latestError
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        serviceJob.cancel()
    }
}
