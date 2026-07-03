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
fun AnalyticsPanel(
    maxDrawdown: String = "N/A",
    portfolioVar: String = "N/A",
    portfolioBeta: String = "N/A",
    volAnn: String = "N/A"
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 16.dp)
    ) {
        Text(
            text = "RISK & ANALYTICS",
            style = MaterialTheme.typography.titleMedium,
            color = TextSecondary,
            modifier = Modifier.padding(start = 16.dp, end = 16.dp, bottom = 12.dp)
        )

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            AnalyticsCard(label = "MAX\nDRAWDOWN", value = maxDrawdown, valueColor = BrandAmber)
            AnalyticsCard(label = "PORTFOLIO\nVAR (95%)", value = portfolioVar, valueColor = BrandAmber)
            AnalyticsCard(label = "PORTFOLIO\nBETA", value = portfolioBeta, valueColor = TextPrimary)
            AnalyticsCard(label = "VOL.\nANN.", value = volAnn, valueColor = TextPrimary)
        }
        
        Spacer(modifier = Modifier.height(16.dp))
        
        // Donut Chart Placeholder
        Box(
            modifier = Modifier
                .padding(horizontal = 16.dp)
                .fillMaxWidth()
                .height(140.dp)
                .clip(RoundedCornerShape(16.dp))
                .background(BgSurfaceRaised),
            contentAlignment = androidx.compose.ui.Alignment.Center
        ) {
            Text("Donut Chart Placeholder", color = TextSecondary)
        }
    }
}

@Composable
fun AnalyticsCard(label: String, value: String, valueColor: androidx.compose.ui.graphics.Color) {
    Column(
        modifier = Modifier
            .width(100.dp)
            .height(80.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(BgSurfaceRaised)
            .padding(12.dp),
        verticalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = TextSecondary
        )
        Text(
            text = value,
            style = MaterialTheme.typography.bodyLarge,
            color = valueColor
        )
    }
}
