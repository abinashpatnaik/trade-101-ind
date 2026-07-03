package com.example.alphatrader.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.example.alphatrader.theme.*

enum class MetricVariant(val topColor: Color) {
    NAV(BrandGreen),
    CASH(BrandPurple),
    POSITIONS(BrandAmber)
}

@Composable
fun MetricCard(
    variant: MetricVariant,
    label: String,
    value: String,
    subLabel: String,
    isPositiveDelta: Boolean? = null
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(100.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(BgSurface)
            .drawBehind {
                drawLine(
                    color = variant.topColor,
                    start = Offset(0f, 0f),
                    end = Offset(size.width, 0f),
                    strokeWidth = 2.dp.toPx()
                )
            }
            .border(1.dp, BorderSubtle, RoundedCornerShape(16.dp))
            .padding(16.dp)
    ) {
        Column(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.SpaceBetween
        ) {
            Text(
                text = label.uppercase(),
                color = TextSecondary,
                style = MaterialTheme.typography.bodyMedium
            )
            
            Text(
                text = value,
                color = TextPrimary,
                style = MaterialTheme.typography.displayLarge
            )
            
            val subLabelColor = when (isPositiveDelta) {
                true -> BrandGreen
                false -> BrandRed
                null -> TextSecondary
            }
            val icon = when (isPositiveDelta) {
                true -> "▲"
                false -> "▼"
                null -> ""
            }
            
            Text(
                text = if (icon.isNotEmpty()) "$subLabel $icon" else subLabel,
                color = subLabelColor,
                style = MaterialTheme.typography.bodyMedium
            )
        }
    }
}
