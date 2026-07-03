package com.example.alphatrader.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.alphatrader.theme.BgPrimary
import com.example.alphatrader.theme.BrandGreen
import com.example.alphatrader.theme.TextSecondary
import com.example.alphatrader.ui.components.*
import com.example.alphatrader.ui.viewmodels.DashboardViewModel

@Composable
fun SignalsScreen(viewModel: DashboardViewModel = viewModel()) {
    val state by viewModel.uiState.collectAsState()

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(BgPrimary)
    ) {
        if (state.isLoading) {
            CircularProgressIndicator(
                modifier = Modifier.align(Alignment.Center),
                color = BrandGreen
            )
        } else if (state.errorMessage != null) {
            Text(
                text = state.errorMessage ?: "Unknown Error",
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.align(Alignment.Center)
            )
        } else {
            val currencySymbol = if (state.marketRegion == MarketRegion.US) "$" else "₹"
            
            val winRate = state.portfolio?.winRate ?: 50.0
            val tradesToday = state.portfolio?.tradesToday ?: state.signals.size
            val avgConf = if (state.signals.isNotEmpty()) state.signals.map { it.confidence }.average() * 100 else 0.0

            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(bottom = 80.dp)
            ) {
                // 1. Intelligence Metrics
                item {
                    Text(
                        text = "AGENT INTELLIGENCE",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextSecondary,
                        modifier = Modifier.padding(start = 16.dp, top = 24.dp, bottom = 4.dp)
                    )
                    IntelligenceMetricsRow(
                        winRate = winRate,
                        tradesToday = tradesToday,
                        avgConfidence = avgConf
                    )
                }

                // 2. Signals Filter
                item {
                    Spacer(modifier = Modifier.height(16.dp))
                    FilterChipRow()
                }

                // 3. Signals Title
                item {
                    Text(
                        text = "MARKET SIGNALS",
                        style = MaterialTheme.typography.headlineMedium,
                        color = MaterialTheme.colorScheme.onBackground,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
                    )
                }

                // 4. Signals List
                items(state.signals) { signalResponse ->
                    val actionEnum = try {
                        SignalAction.valueOf(signalResponse.signal.uppercase())
                    } catch (e: Exception) {
                        SignalAction.HOLD
                    }
                    
                    val conf = (signalResponse.confidence * 100).toInt()
                    val bullPct = if (actionEnum == SignalAction.BUY) conf else if (actionEnum == SignalAction.SELL) 100 - conf else 50
                    val bearPct = if (actionEnum == SignalAction.SELL) conf else if (actionEnum == SignalAction.BUY) 100 - conf else 50
                    val reasonStr = signalResponse.aiReason ?: signalResponse.holdReason ?: "Algo"

                    SignalRow(
                        signal = SignalItem(
                            ticker = signalResponse.symbol,
                            price = signalResponse.price,
                            score = signalResponse.trendScore,
                            action = actionEnum,
                            reason = reasonStr,
                            bullPct = bullPct,
                            bearPct = bearPct
                        ),
                        currencySymbol = currencySymbol
                    )
                }
            }
        }
    }
}
