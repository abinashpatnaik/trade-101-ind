package com.example.alphatrader.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Divider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.alphatrader.theme.*

enum class SignalAction { BUY, SELL, HOLD, GATED }

data class SignalItem(
    val ticker: String,
    val price: Double,
    val score: Double,
    val action: SignalAction,
    val reason: String,
    val bullPct: Int,
    val bearPct: Int
)

@Composable
fun SignalRow(signal: SignalItem, currencySymbol: String = "$") {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .height(88.dp)
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.SpaceBetween
    ) {
        // Top Row: Ticker & Price
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = signal.ticker,
                color = BrandBlue,
                style = MaterialTheme.typography.titleMedium
            )
            Text(
                text = "$currencySymbol${String.format("%.2f", signal.price)}",
                color = TextPrimary,
                style = MaterialTheme.typography.titleMedium
            )
        }

        // Middle Row: Score & Badge
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "Score: ${String.format("%.3f", signal.score)}",
                color = TextSecondary,
                style = MaterialTheme.typography.bodyMedium
            )
            Spacer(modifier = Modifier.width(16.dp))
            SignalBadge(action = signal.action, text = signal.reason)
        }

        // Bottom Row: ML Progress Bar
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("▲ ${signal.bullPct}%", color = BrandGreen, style = MaterialTheme.typography.bodyMedium)
            Spacer(modifier = Modifier.width(8.dp))
            
            // Progress Bar
            Row(
                modifier = Modifier
                    .weight(1f)
                    .height(6.dp)
                    .clip(RoundedCornerShape(3.dp))
                    .background(BgSurfaceRaised)
            ) {
                Box(
                    modifier = Modifier
                        .fillMaxHeight()
                        .weight(signal.bullPct.toFloat() / 100f)
                        .background(BrandGreen)
                )
                Box(
                    modifier = Modifier
                        .fillMaxHeight()
                        .weight(signal.bearPct.toFloat() / 100f)
                        .background(BrandRed)
                )
            }
            
            Spacer(modifier = Modifier.width(8.dp))
            Text("${signal.bearPct}% ▼", color = BrandRed, style = MaterialTheme.typography.bodyMedium)
        }
    }
    androidx.compose.material3.HorizontalDivider(color = BorderSubtle, thickness = 1.dp)
}

@Composable
fun SignalBadge(action: SignalAction, text: String) {
    val (bgColor, textColor) = when (action) {
        SignalAction.BUY -> Pair(BrandGreenDim, BrandGreen)
        SignalAction.SELL -> Pair(BrandRedDim, BrandRed)
        SignalAction.HOLD -> Pair(BgSurfaceRaised, TextSecondary)
        SignalAction.GATED -> Pair(BrandAmberDim, BrandAmber)
    }

    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .background(bgColor)
            .padding(horizontal = 8.dp, vertical = 2.dp)
    ) {
        Text(
            text = text.uppercase(),
            color = textColor,
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Bold
        )
    }
}
