package com.example.alphatrader.ui.components

import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.alphatrader.theme.*

@Composable
fun ExecutionHistoryRow(
    timestamp: String,
    ticker: String,
    action: SignalAction,
    entry: Double,
    exit: Double,
    pnl: Double,
    currencySymbol: String = "$"
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.Top
    ) {
        // Timestamp
        Text(
            text = timestamp,
            style = MonoTextStyle,
            color = TextSecondary,
            modifier = Modifier.width(64.dp)
        )
        
        Spacer(modifier = Modifier.width(12.dp))
        
        // Left Column: Ticker and Action
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = ticker,
                    style = MaterialTheme.typography.titleMedium,
                    color = BrandBlue
                )
                Spacer(modifier = Modifier.width(8.dp))
                SignalBadge(action = action, text = action.name)
            }
        }
        
        // Right Column: Entry, Exit, PnL (Right Aligned)
        Column(horizontalAlignment = Alignment.End) {
            Text(
                text = "Entry: $currencySymbol${String.format("%.2f", entry)}",
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary
            )
            Spacer(modifier = Modifier.height(2.dp))
            Text(
                text = "Exit:  $currencySymbol${String.format("%.2f", exit)}",
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary
            )
            Spacer(modifier = Modifier.height(2.dp))
            
            val pnlColor = if (pnl >= 0) BrandGreen else BrandRed
            val pnlIcon = if (pnl >= 0) "▲" else "▼"
            Text(
                text = "PnL:   ${if(pnl >= 0) "+" else "-"}$currencySymbol${String.format("%.2f", Math.abs(pnl))} $pnlIcon",
                style = MaterialTheme.typography.bodySmall,
                color = pnlColor
            )
        }
    }
    androidx.compose.material3.HorizontalDivider(color = BorderSubtle, thickness = 1.dp)
}
