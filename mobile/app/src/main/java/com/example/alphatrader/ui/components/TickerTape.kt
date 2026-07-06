package com.example.alphatrader.ui.components

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.MaterialTheme
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
fun TickerTape(tickers: List<TickerItem>, currencySymbol: String = "$", onTickerClick: (String) -> Unit = {}) {
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
            .background(MaterialTheme.colorScheme.surface)
            .horizontalScroll(scrollState),
        verticalAlignment = Alignment.CenterVertically
    ) {
        // Repeat list to simulate infinite loop
        val displayTickers = if (tickers.isEmpty()) emptyList() else (1..10).flatMap { tickers }
        
        displayTickers.forEach { ticker ->
            TickerTapeItem(ticker, currencySymbol, onTickerClick)
        }
    }
}

@Composable
fun TickerTapeItem(ticker: TickerItem, currencySymbol: String, onTickerClick: (String) -> Unit) {
    val isUp = ticker.changePct >= 0
    val changeColor = if (isUp) BrandGreen else BrandRed
    val icon = if (isUp) "▲" else "▼"
    
    // Track previous price to know flash direction
    var previousPrice by remember { mutableStateOf(ticker.price) }
    val defaultBg = androidx.compose.ui.graphics.Color.Transparent
    val flashColor = remember { androidx.compose.animation.Animatable(defaultBg) }
    
    LaunchedEffect(ticker.price) {
        if (ticker.price != previousPrice) {
            val isPriceUp = ticker.price > previousPrice
            previousPrice = ticker.price
            val flash = if (isPriceUp) BrandGreen.copy(alpha = 0.5f) else BrandRed.copy(alpha = 0.5f)
            flashColor.snapTo(flash)
            flashColor.animateTo(
                targetValue = defaultBg,
                animationSpec = tween(durationMillis = 1000)
            )
        }
    }

    Row(modifier = Modifier
        .background(flashColor.value)
        .clickable { onTickerClick(ticker.symbol) }) {
        Spacer(modifier = Modifier.width(16.dp))
        Text(
            text = "${ticker.symbol} $currencySymbol${String.format("%.2f", ticker.price)}",
            color = MaterialTheme.colorScheme.onSurface,
            style = MonoTextStyle
        )
        Spacer(modifier = Modifier.width(8.dp))
        Text(
            text = "$icon ${String.format("%.2f", Math.abs(ticker.changePct))}%",
            color = changeColor,
            style = MonoTextStyle
        )
        Spacer(modifier = Modifier.width(16.dp))
        Text(
            text = "|",
            color = MaterialTheme.colorScheme.outlineVariant,
            style = MonoTextStyle
        )
    }
}
