package com.example.tradingagent.ui.signals

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.tradingagent.theme.LossRed
import com.example.tradingagent.theme.ProfitGreen
import com.example.tradingagent.theme.SignalBuyGreen
import com.example.tradingagent.theme.SignalCoolingBlue
import com.example.tradingagent.theme.SignalNeutralGray
import com.example.tradingagent.theme.SignalSellRed
import com.example.tradingagent.theme.SignalWarmingOrange
import java.text.NumberFormat
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SignalsScreen(
    viewModel: SignalsViewModel = viewModel(),
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Market Signals") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
                actions = {
                    IconButton(onClick = { viewModel.refresh() }) {
                        Icon(Icons.Default.Refresh, contentDescription = "Refresh")
                    }
                },
            )
        },
        modifier = modifier,
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
        ) {
            AnimatedVisibility(visible = state.isLoading) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            }

            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(
                    horizontal = 16.dp,
                    vertical = 8.dp,
                ),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(state.signals, key = { it.symbol }) { signal ->
                    SignalCard(signal = signal)
                }
            }
        }
    }
}

@Composable
private fun SignalCard(signal: Signal) {
    val currencyFormat = NumberFormat.getCurrencyInstance(Locale("en", "IN")).apply {
        maximumFractionDigits = 2
        minimumFractionDigits = 2
    }
    val changeColor = if (signal.changePct >= 0) ProfitGreen else LossRed
    val changePrefix = if (signal.changePct >= 0) "+" else ""
    val trendZone = getTrendZone(signal.trendScore)

    ElevatedCard(
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
        ) {
            // Top row: Symbol + Price and Change
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text(
                        text = signal.symbol,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(modifier = Modifier.height(2.dp))
                    Text(
                        text = currencyFormat.format(signal.price),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Text(
                    text = "${changePrefix}${String.format(Locale.US, "%.2f", signal.changePct)}%",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = changeColor,
                )
            }

            Spacer(modifier = Modifier.height(12.dp))

            // Trend Score Bar
            Column {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = "Trend Score",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        text = trendZone.label,
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.SemiBold,
                        color = trendZone.color,
                    )
                }
                Spacer(modifier = Modifier.height(4.dp))
                TrendScoreBar(
                    score = signal.trendScore,
                    color = trendZone.color,
                    modifier = Modifier.fillMaxWidth(),
                )
            }

            Spacer(modifier = Modifier.height(12.dp))

            // Badges row
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                SignalBadge(
                    label = signal.signal,
                    color = getSignalColor(signal.signal),
                )
                AiDecisionBadge(
                    label = "AI: ${signal.aiDecision}",
                    decision = signal.aiDecision,
                )
                Spacer(modifier = Modifier.weight(1f))
                Text(
                    text = String.format(Locale.US, "%.2f", signal.trendScore),
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold,
                    color = trendZone.color,
                )
            }
        }
    }
}

@Composable
private fun TrendScoreBar(
    score: Double,
    color: Color,
    modifier: Modifier = Modifier,
) {
    // Map score from [-1, 1] to [0, 1] for progress
    val progress = ((score + 1.0) / 2.0).toFloat().coerceIn(0f, 1f)

    Box(
        modifier = modifier
            .height(6.dp)
            .clip(RoundedCornerShape(3.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth(progress)
                .height(6.dp)
                .clip(RoundedCornerShape(3.dp))
                .background(color),
        )
    }
}

@Composable
private fun SignalBadge(label: String, color: Color) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(color.copy(alpha = 0.15f))
            .padding(horizontal = 10.dp, vertical = 4.dp),
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = color,
        )
    }
}

@Composable
private fun AiDecisionBadge(label: String, decision: String) {
    val color = when (decision) {
        "BUY" -> ProfitGreen
        "SELL" -> LossRed
        "HOLD" -> SignalWarmingOrange
        else -> SignalNeutralGray
    }
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(MaterialTheme.colorScheme.secondaryContainer)
            .padding(horizontal = 10.dp, vertical = 4.dp),
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = color,
        )
    }
}

private data class TrendZone(val label: String, val color: Color)

private fun getTrendZone(score: Double): TrendZone {
    return when {
        score >= 0.55 -> TrendZone("BUY zone", SignalBuyGreen)
        score in 0.40..0.55 -> TrendZone("Warming up", SignalWarmingOrange)
        score in -0.40..0.40 -> TrendZone("Neutral", SignalNeutralGray)
        score in -0.55..-0.40 -> TrendZone("Cooling down", SignalCoolingBlue)
        else -> TrendZone("SELL zone", SignalSellRed)
    }
}

private fun getSignalColor(signal: String): Color {
    return when (signal) {
        "BUY" -> SignalBuyGreen
        "SELL" -> SignalSellRed
        else -> SignalNeutralGray
    }
}
