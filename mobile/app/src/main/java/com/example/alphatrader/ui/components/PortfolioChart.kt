package com.example.alphatrader.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.example.alphatrader.theme.*

import com.example.alphatrader.data.network.NavHistoryItem

@Composable
fun PortfolioChartCard(history: List<NavHistoryItem> = emptyList()) {
    var selectedTimeRange by remember { mutableStateOf("1D") }
    val timeRanges = listOf("1D", "1W", "1M", "3M", "1Y")

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(260.dp)
            .padding(horizontal = 16.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(BgSurface)
            .padding(16.dp)
    ) {
        Column(modifier = Modifier.fillMaxSize()) {
            // Header Section
            Column(modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = "PORTFOLIO PERFORMANCE",
                    color = TextSecondary,
                    style = MaterialTheme.typography.titleMedium
                )
                Spacer(modifier = Modifier.height(8.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    timeRanges.forEach { range ->
                        TimeRangeChip(
                            text = range,
                            isSelected = range == selectedTimeRange,
                            onClick = { selectedTimeRange = range }
                        )
                    }
                }
            }
            
            Spacer(modifier = Modifier.height(16.dp))

            // Chart Placeholder due to API differences in Vico 2.x
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center
            ) {
                Text("Chart Placeholder", color = TextSecondary)
            }
        }
    }
}

@Composable
fun TimeRangeChip(text: String, isSelected: Boolean, onClick: () -> Unit) {
    val bgColor = if (isSelected) BrandGreen else BgSurfaceRaised
    val textColor = if (isSelected) Color.Black else TextSecondary

    Box(
        modifier = Modifier
            .height(28.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(bgColor)
            .clickable { onClick() }
            .padding(horizontal = 12.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = text,
            color = textColor,
            style = if (isSelected) MaterialTheme.typography.labelMedium.copy(fontWeight = androidx.compose.ui.text.font.FontWeight.Bold) else MaterialTheme.typography.labelMedium
        )
    }
}
