package com.example.alphatrader.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.alphatrader.theme.BgPrimary
import com.example.alphatrader.theme.TextSecondary
import com.example.alphatrader.ui.components.DecisionLogEntry
import com.example.alphatrader.ui.components.ExecutionHistoryRow
import com.example.alphatrader.ui.components.SignalAction

import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.alphatrader.ui.viewmodels.DashboardViewModel

@Composable
fun LogsScreen(viewModel: DashboardViewModel = viewModel()) {
    val state by viewModel.uiState.collectAsState()

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(BgPrimary)
    ) {
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(bottom = 80.dp)
        ) {
            // 1. Decision Log
            item {
                Text(
                    text = "DECISION LOG",
                    style = MaterialTheme.typography.titleMedium,
                    color = TextSecondary,
                    modifier = Modifier.padding(start = 16.dp, top = 24.dp, bottom = 4.dp)
                )
            }
            items(state.decisionLogs) { log ->
                DecisionLogEntry(
                    icon = log.icon,
                    title = log.title,
                    subtitle = log.subtitle,
                    timestamp = log.timestamp
                )
            }

            // 2. Execution History
            item {
                Text(
                    text = "EXECUTION HISTORY",
                    style = MaterialTheme.typography.titleMedium,
                    color = TextSecondary,
                    modifier = Modifier.padding(start = 16.dp, top = 32.dp, bottom = 4.dp)
                )
            }
            
            val currencySymbol = if (state.marketRegion == com.example.alphatrader.ui.components.MarketRegion.US) "$" else "₹"
            
            items(state.executionLogs) { log ->
                ExecutionHistoryRow(
                    timestamp = log.timestamp,
                    ticker = log.ticker,
                    action = log.action,
                    entry = log.entry,
                    exit = log.exit,
                    pnl = log.pnl,
                    currencySymbol = currencySymbol
                )
            }
        }
    }
}
