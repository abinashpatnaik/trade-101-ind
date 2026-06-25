package com.example.tradingagent

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.example.tradingagent.theme.TradingAgentTheme
import com.example.tradingagent.ui.TradingApp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        enableEdgeToEdge()
        setContent {
            TradingAgentTheme {
                TradingApp()
            }
        }
    }
}
