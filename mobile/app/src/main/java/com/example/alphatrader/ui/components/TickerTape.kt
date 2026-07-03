package com.example.alphatrader.ui.components

import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.alphatrader.theme.*

data class TickerItem(
    val symbol: String,
    val price: Double,
    val changePct: Double
)

@Composable
fun TickerTape(tickers: List<TickerItem>, currencySymbol: String = "$") {
    // Basic auto-scroll implementation
    val scrollState = rememberScrollState()
    
    LaunchedEffect(tickers) {
        if (tickers.isNotEmpty()) {
            while (true) {
                // simple auto scroll, in reality we'd measure width and scroll infinitely
                scrollState.animateScrollTo(
                    value = scrollState.maxValue,
                    animationSpec = tween(
                        durationMillis = scrollState.maxValue * 16, // roughly 60dp/s
                        easing = LinearEasing
                    )
                )
                scrollState.scrollTo(0)
            }
        }
    }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(36.dp)
            .background(BgSurface)
            .horizontalScroll(scrollState),
        verticalAlignment = Alignment.CenterVertically
    ) {
        // Repeat list to simulate infinite loop
        val displayTickers = if (tickers.isEmpty()) emptyList() else (1..10).flatMap { tickers }
        
        displayTickers.forEach { ticker ->
            val isUp = ticker.changePct >= 0
            val color = if (isUp) BrandGreen else BrandRed
            val icon = if (isUp) "▲" else "▼"
            
            Spacer(modifier = Modifier.width(16.dp))
            Text(
                text = "${ticker.symbol} $currencySymbol${String.format("%.2f", ticker.price)}",
                color = TextPrimary,
                style = MonoTextStyle
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = "$icon ${String.format("%.2f", Math.abs(ticker.changePct))}%",
                color = color,
                style = MonoTextStyle
            )
            Spacer(modifier = Modifier.width(16.dp))
            Text(
                text = "|",
                color = TextDisabled,
                style = MonoTextStyle
            )
        }
    }
}
