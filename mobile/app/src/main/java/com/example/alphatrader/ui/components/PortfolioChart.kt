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
import com.patrykandpatrick.vico.compose.cartesian.CartesianChartHost
import com.patrykandpatrick.vico.compose.cartesian.axis.rememberBottom
import com.patrykandpatrick.vico.compose.cartesian.axis.rememberStart
import com.patrykandpatrick.vico.compose.cartesian.layer.rememberLineCartesianLayer
import com.patrykandpatrick.vico.compose.cartesian.rememberCartesianChart
import com.patrykandpatrick.vico.core.cartesian.axis.HorizontalAxis
import com.patrykandpatrick.vico.core.cartesian.axis.VerticalAxis
import com.patrykandpatrick.vico.core.cartesian.data.lineSeries
import com.patrykandpatrick.vico.core.cartesian.data.CartesianChartModelProducer
import com.patrykandpatrick.vico.core.cartesian.Zoom
import com.patrykandpatrick.vico.compose.cartesian.rememberVicoZoomState
import com.patrykandpatrick.vico.compose.cartesian.rememberVicoScrollState

@Composable
fun PortfolioChartCard(history: List<NavHistoryItem> = emptyList()) {
    var selectedTimeRange by remember { mutableStateOf("1D") }
    val timeRanges = listOf("1D", "1W", "1M", "3M", "1Y")
    
    val modelProducer = remember { CartesianChartModelProducer() }

    LaunchedEffect(history) {
        if (history.isNotEmpty()) {
            modelProducer.runTransaction {
                lineSeries {
                    series(history.map { it.nav })
                }
            }
        }
    }

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(260.dp)
            .padding(horizontal = 16.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(MaterialTheme.colorScheme.surface)
            .padding(16.dp)
    ) {
        Column(modifier = Modifier.fillMaxSize()) {
            // Header Section
            Column(modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = "PORTFOLIO PERFORMANCE",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
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

            if (history.isEmpty()) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Text("No data available", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
                val myRangeProvider = remember {
                    object : com.patrykandpatrick.vico.core.cartesian.data.CartesianLayerRangeProvider {
                        override fun getMinX(minX: Double, maxX: Double, extraStore: com.patrykandpatrick.vico.core.common.data.ExtraStore) = minX
                        override fun getMaxX(minX: Double, maxX: Double, extraStore: com.patrykandpatrick.vico.core.common.data.ExtraStore) = maxX
                        override fun getMinY(minY: Double, maxY: Double, extraStore: com.patrykandpatrick.vico.core.common.data.ExtraStore) = minY * 0.95
                        override fun getMaxY(minY: Double, maxY: Double, extraStore: com.patrykandpatrick.vico.core.common.data.ExtraStore) = maxY * 1.05
                    }
                }
                CartesianChartHost(
                    chart = rememberCartesianChart(
                        rememberLineCartesianLayer(
                            rangeProvider = myRangeProvider
                        ),
                        startAxis = VerticalAxis.rememberStart(),
                        bottomAxis = HorizontalAxis.rememberBottom(),
                    ),
                    modelProducer = modelProducer,
                    zoomState = rememberVicoZoomState(zoomEnabled = false, initialZoom = Zoom.Content, maxZoom = Zoom.Content),
                    scrollState = rememberVicoScrollState(scrollEnabled = false),
                    modifier = Modifier.fillMaxSize()
                )
            }
        }
    }
}

@Composable
fun TimeRangeChip(text: String, isSelected: Boolean, onClick: () -> Unit) {
    val bgColor = if (isSelected) BrandGreen else MaterialTheme.colorScheme.surfaceVariant
    val textColor = if (isSelected) Color.Black else MaterialTheme.colorScheme.onSurfaceVariant

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
