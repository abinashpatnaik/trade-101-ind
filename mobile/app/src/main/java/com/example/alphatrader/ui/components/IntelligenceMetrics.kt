package com.example.alphatrader.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import com.example.alphatrader.theme.*

@Composable
fun IntelligenceMetricsRow(
    winRate: Double,
    tradesToday: Int,
    avgConfidence: Double
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        IntelligenceMetricCard(label = "PREDICTION\nACC.", value = "${String.format("%.1f", winRate)}%")
        IntelligenceMetricCard(label = "SIGNALS\nTODAY", value = tradesToday.toString())
        IntelligenceMetricCard(label = "AVG\nCONF.", value = "${String.format("%.1f", avgConfidence)}%")
    }
}

@Composable
fun IntelligenceMetricCard(label: String, value: String) {
    Column(
        modifier = Modifier
            .width(110.dp)
            .height(80.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(12.dp),
        verticalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Text(
            text = value,
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurface
        )
    }
}
