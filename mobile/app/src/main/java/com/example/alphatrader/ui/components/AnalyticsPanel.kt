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

import androidx.compose.foundation.Canvas
import androidx.compose.ui.graphics.drawscope.Stroke

@Composable
fun AnalyticsPanel(
    maxDrawdown: String = "N/A",
    portfolioVar: String = "N/A",
    portfolioBeta: String = "N/A",
    volAnn: String = "N/A",
    winRate: Double = 0.0
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 16.dp)
    ) {
        Text(
            text = "RISK & ANALYTICS",
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
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
            AnalyticsCard(label = "PORTFOLIO\nBETA", value = portfolioBeta, valueColor = MaterialTheme.colorScheme.onSurface)
            AnalyticsCard(label = "VOL.\nANN.", value = volAnn, valueColor = MaterialTheme.colorScheme.onSurface)
        }
        
        Spacer(modifier = Modifier.height(16.dp))
        
        // Win Rate Donut Chart
        Box(
            modifier = Modifier
                .padding(horizontal = 16.dp)
                .fillMaxWidth()
                .height(140.dp)
                .clip(RoundedCornerShape(16.dp))
                .background(MaterialTheme.colorScheme.surfaceVariant),
            contentAlignment = androidx.compose.ui.Alignment.Center
        ) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 32.dp),
                verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceAround
            ) {
                // Donut Chart
                Box(contentAlignment = androidx.compose.ui.Alignment.Center, modifier = Modifier.size(100.dp)) {
                    val winAngle = ((winRate / 100f) * 360f).toFloat()
                    val lossAngle = 360f - winAngle
                    val strokeWidth = 16.dp
                    Canvas(modifier = Modifier.fillMaxSize().padding(strokeWidth / 2)) {
                        drawArc(
                            color = BrandRed,
                            startAngle = -90f + winAngle,
                            sweepAngle = lossAngle,
                            useCenter = false,
                            style = Stroke(width = strokeWidth.toPx(), cap = androidx.compose.ui.graphics.StrokeCap.Round)
                        )
                        drawArc(
                            color = BrandGreen,
                            startAngle = -90f,
                            sweepAngle = winAngle,
                            useCenter = false,
                            style = Stroke(width = strokeWidth.toPx(), cap = androidx.compose.ui.graphics.StrokeCap.Round)
                        )
                    }
                    Text(
                        text = "${winRate.toInt()}%",
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }

                // Legend
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                        Box(modifier = Modifier.size(12.dp).clip(androidx.compose.foundation.shape.CircleShape).background(BrandGreen))
                        Spacer(modifier = Modifier.width(8.dp))
                        Text("Win", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                        Box(modifier = Modifier.size(12.dp).clip(androidx.compose.foundation.shape.CircleShape).background(BrandRed))
                        Spacer(modifier = Modifier.width(8.dp))
                        Text("Loss", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
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
            color = valueColor
        )
    }
}
