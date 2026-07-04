package com.example.alphatrader.data

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import androidx.core.app.NotificationCompat
import com.example.alphatrader.R

class NotificationHelper(private val context: Context) {
    private val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    init {
        createChannels()
    }

    private fun createChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val foregroundChannel = NotificationChannel(
                CHANNEL_ID_FOREGROUND,
                "Service Status",
                NotificationManager.IMPORTANCE_LOW
            ).apply { description = "Ongoing notification for background polling" }
            
            val tradeChannel = NotificationChannel(
                CHANNEL_ID_TRADES,
                "Trade Execution",
                NotificationManager.IMPORTANCE_HIGH
            ).apply { description = "Alerts for new trades" }

            val statusChannel = NotificationChannel(
                CHANNEL_ID_STATUS,
                "Agent Status",
                NotificationManager.IMPORTANCE_HIGH
            ).apply { description = "Alerts when agent wakes up or sleeps" }

            val errorChannel = NotificationChannel(
                CHANNEL_ID_ERRORS,
                "Server Health",
                NotificationManager.IMPORTANCE_HIGH
            ).apply { description = "Alerts for severe server errors" }

            notificationManager.createNotificationChannels(
                listOf(foregroundChannel, tradeChannel, statusChannel, errorChannel)
            )
        }
    }

    fun buildForegroundNotification(statusText: String): Notification {
        return NotificationCompat.Builder(context, CHANNEL_ID_FOREGROUND)
            .setContentTitle("AlphaTrader")
            .setContentText(statusText)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setOngoing(true)
            .build()
    }

    fun notifyTrade(market: String, symbol: String, action: String, price: Double) {
        val notification = NotificationCompat.Builder(context, CHANNEL_ID_TRADES)
            .setContentTitle("Trade Executed ($market)")
            .setContentText("$action $symbol at $$price")
            .setSmallIcon(R.mipmap.ic_launcher)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()
        notificationManager.notify(System.currentTimeMillis().toInt(), notification)
    }

    fun notifyAgentStatus(market: String, status: String) {
        val notification = NotificationCompat.Builder(context, CHANNEL_ID_STATUS)
            .setContentTitle("Agent Status ($market)")
            .setContentText("Agent is now $status")
            .setSmallIcon(R.mipmap.ic_launcher)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()
        notificationManager.notify(System.currentTimeMillis().toInt(), notification)
    }

    fun notifyError(market: String, message: String) {
        val notification = NotificationCompat.Builder(context, CHANNEL_ID_ERRORS)
            .setContentTitle("Server Error ($market)")
            .setContentText(message)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()
        notificationManager.notify(System.currentTimeMillis().toInt(), notification)
    }

    companion object {
        const val CHANNEL_ID_FOREGROUND = "channel_foreground"
        const val CHANNEL_ID_TRADES = "channel_trades"
        const val CHANNEL_ID_STATUS = "channel_status"
        const val CHANNEL_ID_ERRORS = "channel_errors"
    }
}
