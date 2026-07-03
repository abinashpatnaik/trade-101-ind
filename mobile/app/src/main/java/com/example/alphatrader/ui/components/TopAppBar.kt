package com.example.alphatrader.ui.components

import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.LightMode
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.alphatrader.theme.*

enum class AgentStatus {
    LIVE, CLOSED, SLEEPING
}

enum class MarketRegion {
    US, IN
}

@Composable
fun AlphaTopAppBar(
    marketRegion: MarketRegion,
    onMarketToggle: () -> Unit,
    status: AgentStatus,
    statusText: String
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(64.dp)
            .background(BgPrimary)
            .padding(horizontal = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        // Logo and Title
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.clickable { onMarketToggle() }
        ) {
            Text(
                text = "⚡",
                color = BrandGreen,
                style = MaterialTheme.typography.headlineLarge
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = "Alpha Trader",
                color = TextPrimary,
                style = MaterialTheme.typography.headlineLarge
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = if (marketRegion == MarketRegion.US) "🇺🇸" else "🇮🇳",
                style = MaterialTheme.typography.headlineLarge
            )
        }

        // Status Row
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                imageVector = Icons.Default.LightMode,
                contentDescription = "Theme Toggle",
                tint = TextSecondary,
                modifier = Modifier.size(24.dp)
            )
            Spacer(modifier = Modifier.width(16.dp))
            StatusBadge(status = status, text = statusText)
        }
    }
}

@Composable
fun StatusBadge(status: AgentStatus, text: String) {
    val bgColor = when (status) {
        AgentStatus.LIVE -> BrandGreenDim
        AgentStatus.CLOSED -> BrandRedDim
        AgentStatus.SLEEPING -> BgSurfaceRaised
    }
    val textColor = when (status) {
        AgentStatus.LIVE -> BrandGreen
        AgentStatus.CLOSED -> BrandRed
        AgentStatus.SLEEPING -> TextSecondary
    }

    val infiniteTransition = rememberInfiniteTransition(label = "pulse")
    val alpha by infiniteTransition.animateFloat(
        initialValue = 0.2f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(1000, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "pulseAlpha"
    )

    Row(
        modifier = Modifier
            .height(28.dp)
            .clip(RoundedCornerShape(14.dp))
            .background(bgColor)
            .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (status == AgentStatus.LIVE || status == AgentStatus.CLOSED) {
            Box(
                modifier = Modifier
                    .size(6.dp)
                    .clip(RoundedCornerShape(3.dp))
                    .background(textColor)
                    .alpha(if (status == AgentStatus.LIVE) alpha else 1f)
            )
            Spacer(modifier = Modifier.width(6.dp))
        }
        Text(
            text = text,
            color = textColor,
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Bold
        )
    }
}
