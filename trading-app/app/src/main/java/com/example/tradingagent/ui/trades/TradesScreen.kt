package com.example.tradingagent.ui.trades

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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Receipt
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.tradingagent.theme.LossRed
import com.example.tradingagent.theme.ProfitGreen
import java.text.NumberFormat
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TradesScreen(
    viewModel: TradesViewModel = viewModel(),
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsState()
    val groupedTrades by remember(state.trades) {
        derivedStateOf {
            state.trades.groupBy { it.date }
                .toSortedMap(compareByDescending { it })
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Trade History") },
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

            // Filter chips
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                FilterChip(
                    selected = state.selectedFilter == TradeFilter.TODAY,
                    onClick = { viewModel.setFilter(TradeFilter.TODAY) },
                    label = { Text("Today") },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = MaterialTheme.colorScheme.primaryContainer,
                        selectedLabelColor = MaterialTheme.colorScheme.onPrimaryContainer,
                    ),
                )
                FilterChip(
                    selected = state.selectedFilter == TradeFilter.ALL_HISTORY,
                    onClick = { viewModel.setFilter(TradeFilter.ALL_HISTORY) },
                    label = { Text("All History") },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = MaterialTheme.colorScheme.primaryContainer,
                        selectedLabelColor = MaterialTheme.colorScheme.onPrimaryContainer,
                    ),
                )
            }

            if (state.trades.isEmpty()) {
                EmptyTradesState(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(16.dp),
                )
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(
                        horizontal = 16.dp,
                        vertical = 8.dp,
                    ),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    groupedTrades.forEach { (date, trades) ->
                        item(key = "header_$date") {
                            DateHeader(date = date)
                        }
                        items(trades, key = { it.id }) { trade ->
                            TradeRow(trade = trade)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun DateHeader(date: String) {
    val formattedDate = formatDate(date)
    Text(
        text = formattedDate,
        style = MaterialTheme.typography.labelLarge,
        fontWeight = FontWeight.Bold,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
    )
}

@Composable
private fun TradeRow(trade: Trade) {
    val currencyFormat = NumberFormat.getCurrencyInstance(Locale("en", "IN")).apply {
        maximumFractionDigits = 2
        minimumFractionDigits = 2
    }

    OutlinedCard(
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
        ) {
            // Top row: Action badge + Symbol + Mode badge
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    ActionBadge(action = trade.action)
                    Column {
                        Text(
                            text = trade.symbol,
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold,
                        )
                        Text(
                            text = trade.time,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                ModeBadge(mode = trade.mode)
            }

            Spacer(modifier = Modifier.height(8.dp))
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Spacer(modifier = Modifier.height(8.dp))

            // Bottom row: Qty, Price, P&L
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                DetailItem(label = "Qty", value = trade.quantity.toString())
                DetailItem(label = "Price", value = currencyFormat.format(trade.price))
                if (trade.pnl != null) {
                    val pnlColor = if (trade.pnl >= 0) ProfitGreen else LossRed
                    val pnlPrefix = if (trade.pnl >= 0) "+" else ""
                    DetailItem(
                        label = "P&L",
                        value = "$pnlPrefix${currencyFormat.format(trade.pnl)}",
                        valueColor = pnlColor,
                    )
                } else {
                    DetailItem(
                        label = "P&L",
                        value = "—",
                        valueColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun ActionBadge(action: String) {
    val color = if (action == "BUY") ProfitGreen else LossRed
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(color.copy(alpha = 0.15f))
            .padding(horizontal = 8.dp, vertical = 4.dp),
    ) {
        Text(
            text = action,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = color,
        )
    }
}

@Composable
private fun ModeBadge(mode: String) {
    val color = if (mode == "LIVE") ProfitGreen else Color.Gray
    val bgColor = if (mode == "LIVE") ProfitGreen.copy(alpha = 0.15f) else Color.Gray.copy(alpha = 0.12f)
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(bgColor)
            .padding(horizontal = 8.dp, vertical = 4.dp),
    ) {
        Text(
            text = mode,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = color,
        )
    }
}

@Composable
private fun DetailItem(
    label: String,
    value: String,
    valueColor: Color = Color.Unspecified,
) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(modifier = Modifier.height(2.dp))
        Text(
            text = value,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
            color = valueColor,
        )
    }
}

@Composable
private fun EmptyTradesState(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier,
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Icon(
                imageVector = Icons.Default.Receipt,
                contentDescription = null,
                modifier = Modifier.size(64.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f),
            )
            Spacer(modifier = Modifier.height(16.dp))
            Text(
                text = "No trades yet",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = "Executed trades will appear here",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
                textAlign = TextAlign.Center,
            )
        }
    }
}

private fun formatDate(dateStr: String): String {
    // Simple formatting for "2026-06-25" -> "June 25, 2026"
    return try {
        val parts = dateStr.split("-")
        if (parts.size == 3) {
            val months = listOf(
                "", "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December",
            )
            val month = months[parts[1].toInt()]
            val day = parts[2].toInt()
            val year = parts[0]
            "$month $day, $year"
        } else {
            dateStr
        }
    } catch (e: Exception) {
        dateStr
    }
}
