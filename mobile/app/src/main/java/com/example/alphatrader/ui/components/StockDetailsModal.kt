package com.example.alphatrader.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.example.alphatrader.data.network.StockDetailsResponse
import com.example.alphatrader.theme.BrandGreen
import com.example.alphatrader.theme.BrandRed

import com.patrykandpatrick.vico.compose.cartesian.CartesianChartHost
import com.patrykandpatrick.vico.compose.cartesian.axis.rememberBottom
import com.patrykandpatrick.vico.compose.cartesian.axis.rememberStart
import com.patrykandpatrick.vico.compose.cartesian.layer.rememberLineCartesianLayer
import com.patrykandpatrick.vico.compose.cartesian.rememberCartesianChart
import com.patrykandpatrick.vico.core.cartesian.Zoom
import com.patrykandpatrick.vico.compose.cartesian.rememberVicoZoomState
import com.patrykandpatrick.vico.compose.cartesian.rememberVicoScrollState
import com.patrykandpatrick.vico.core.cartesian.axis.HorizontalAxis
import com.patrykandpatrick.vico.core.cartesian.axis.VerticalAxis
import com.patrykandpatrick.vico.core.cartesian.data.CartesianValueFormatter
import com.patrykandpatrick.vico.core.cartesian.data.CartesianChartModelProducer
import com.patrykandpatrick.vico.core.cartesian.data.lineSeries

@Composable
fun StockDetailsModal(
    symbol: String,
    isLoading: Boolean,
    details: StockDetailsResponse?,
    onDismiss: () -> Unit
) {
    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false)
    ) {
        Surface(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
            shape = RoundedCornerShape(16.dp),
            color = MaterialTheme.colorScheme.surface
        ) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(16.dp)
            ) {
                // Header
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = symbol,
                        style = MaterialTheme.typography.headlineMedium,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    IconButton(onClick = onDismiss) {
                        Icon(
                            imageVector = Icons.Default.Close,
                            contentDescription = "Close",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
                
                Spacer(modifier = Modifier.height(16.dp))

                if (isLoading) {
                    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator(color = BrandGreen)
                    }
                } else if (details != null) {
                    LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        verticalArrangement = Arrangement.spacedBy(16.dp)
                    ) {
                        item {
                            Text(
                                text = "6M Performance",
                                style = MaterialTheme.typography.titleMedium,
                                color = MaterialTheme.colorScheme.onSurface
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                            StockChart(chartData = details.chartData)
                        }

                        item {
                            Spacer(modifier = Modifier.height(8.dp))
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                SummaryCard(
                                    title = "TOTAL BOUGHT",
                                    amount = details.summary.totalBought,
                                    modifier = Modifier.weight(1f)
                                )
                                SummaryCard(
                                    title = "TOTAL SOLD",
                                    amount = details.summary.totalSold,
                                    modifier = Modifier.weight(1f)
                                )
                                SummaryCard(
                                    title = "TOTAL PNL",
                                    amount = details.summary.totalPnl,
                                    modifier = Modifier.weight(1f),
                                    isPnl = true
                                )
                            }
                        }

                        item {
                            Spacer(modifier = Modifier.height(16.dp))
                            Text(
                                text = "Recent Transactions",
                                style = MaterialTheme.typography.titleMedium,
                                color = MaterialTheme.colorScheme.onSurface
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                        }

                        if (details.trades.isEmpty()) {
                            item {
                                Box(modifier = Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) {
                                    Text("No recent transactions", color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        } else {
                            items(details.trades) { trade ->
                                TradeRow(trade = trade)
                            }
                        }
                    }
                } else {
                    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text("Failed to load data", color = BrandRed)
                    }
                }
            }
        }
    }
}

@Composable
fun StockChart(chartData: List<com.example.alphatrader.data.network.ChartDataPoint>) {
    val modelProducer = remember { CartesianChartModelProducer() }

    LaunchedEffect(chartData) {
        val prices = chartData.map { it.price }
        if (prices.isNotEmpty()) {
            modelProducer.runTransaction {
                lineSeries { series(prices) }
            }
        }
    }

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(200.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(8.dp)
    ) {
        if (chartData.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("No chart data", color = MaterialTheme.colorScheme.onSurfaceVariant)
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
                    bottomAxis = HorizontalAxis.rememberBottom(
                        valueFormatter = CartesianValueFormatter { context, value, _ ->
                            val index = value.toInt()
                            if (index in chartData.indices) {
                                chartData[index].date.takeLast(5)
                            } else {
                                ""
                            }
                        }
                    ),
                ),
                modelProducer = modelProducer,
                zoomState = rememberVicoZoomState(zoomEnabled = false, initialZoom = Zoom.Content, maxZoom = Zoom.Content),
                scrollState = rememberVicoScrollState(scrollEnabled = false),
                modifier = Modifier.fillMaxSize()
            )
        }
    }
}

@Composable
fun SummaryCard(title: String, amount: Double, modifier: Modifier = Modifier, isPnl: Boolean = false) {
    val amountColor = if (isPnl) {
        if (amount >= 0) BrandGreen else BrandRed
    } else {
        MaterialTheme.colorScheme.onSurface
    }
    
    val prefix = if (isPnl && amount >= 0) "+" else ""

    Column(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(12.dp)
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            fontSize = 10.sp
        )
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = String.format("%s$%.2f", prefix, amount),
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Bold,
            color = amountColor
        )
    }
}

@Composable
fun TradeRow(trade: com.example.alphatrader.data.network.TradeResponse) {
    val isBuy = trade.action == "BUY"
    val actionColor = if (isBuy) BrandGreen else BrandRed
    
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = "${trade.date} ${trade.time}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        Text(
            text = trade.action,
            style = MaterialTheme.typography.labelMedium,
            color = actionColor,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.weight(0.5f)
        )
        Text(
            text = String.format("$%.2f", trade.price),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(0.6f)
        )
        Text(
            text = String.format("%.4f", trade.quantity ?: 1.0),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(0.6f)
        )
    }
}
