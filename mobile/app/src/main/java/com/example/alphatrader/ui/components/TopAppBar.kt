package com.example.alphatrader.ui.components

import com.example.alphatrader.data.network.PortfolioResponse

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

import androidx.compose.material.icons.filled.DarkMode
import androidx.compose.material.icons.filled.SettingsBrightness
import androidx.compose.material3.IconButton
import com.example.alphatrader.data.ThemeMode

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
    portfolio: PortfolioResponse?,
    themeMode: ThemeMode = ThemeMode.SYSTEM,
    onThemeToggle: () -> Unit = {}
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(64.dp)
            .background(MaterialTheme.colorScheme.background)
            .padding(horizontal = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        // Logo and Title
        Row(
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(modifier = Modifier.clickable { onMarketToggle() }, verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = "⚡",
                    color = BrandGreen,
                    style = MaterialTheme.typography.headlineLarge
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = "Alpha",
                    color = MaterialTheme.colorScheme.onBackground,
                    style = MaterialTheme.typography.titleLarge
                )
                Spacer(modifier = Modifier.width(4.dp))
                Text(
                    text = if (marketRegion == MarketRegion.US) "🇺🇸" else "🇮🇳",
                    style = MaterialTheme.typography.titleLarge
                )
            }
            
            Spacer(modifier = Modifier.width(8.dp))
            
            IconButton(onClick = onThemeToggle) {
                Icon(
                    imageVector = when (themeMode) {
                        ThemeMode.SYSTEM -> Icons.Filled.SettingsBrightness
                        ThemeMode.LIGHT -> Icons.Filled.LightMode
                        ThemeMode.DARK -> Icons.Filled.DarkMode
                    },
                    contentDescription = "Toggle Theme",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }

        // Status Row
        Column(
            horizontalAlignment = Alignment.End,
            verticalArrangement = Arrangement.Center
        ) {
            if (portfolio != null) {
                // Market Status Badge
                val marketText = if (portfolio.marketOpen) {
                    "Market Open"
                } else {
                    val nextOpenFormatted = if (!portfolio.nextOpen.isNullOrEmpty()) {
                        try {
                            val zdt = java.time.ZonedDateTime.parse(portfolio.nextOpen)
                            val formatter = java.time.format.DateTimeFormatter.ofPattern("EEE HH:mm")
                            " (Opens ${zdt.format(formatter)})"
                        } catch (e: Exception) {
                            ""
                        }
                    } else ""
                    "Closed$nextOpenFormatted"
                }
                StatusBadge(status = if (portfolio.marketOpen) AgentStatus.LIVE else AgentStatus.CLOSED, text = marketText)
                
                Spacer(modifier = Modifier.height(4.dp))
                
                // Agent Status Badge
                val agentStatusEnum = if (portfolio.agentStatus == "running") AgentStatus.LIVE else AgentStatus.SLEEPING
                val agentText = if (portfolio.agentStatus == "running") "Agent Running" else "Agent Sleeping"
                StatusBadge(status = agentStatusEnum, text = agentText)
            } else {
                StatusBadge(status = AgentStatus.SLEEPING, text = "Connecting...")
            }
        }
    }
}

@Composable
fun StatusBadge(status: AgentStatus, text: String) {
    val bgColor = when (status) {
        AgentStatus.LIVE -> BrandGreenDim
        AgentStatus.CLOSED -> BrandRedDim
        AgentStatus.SLEEPING -> MaterialTheme.colorScheme.surfaceVariant
    }
    val textColor = when (status) {
        AgentStatus.LIVE -> BrandGreen
        AgentStatus.CLOSED -> BrandRed
        AgentStatus.SLEEPING -> MaterialTheme.colorScheme.onSurfaceVariant
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
            .height(22.dp)
            .clip(RoundedCornerShape(11.dp))
            .background(bgColor)
            .padding(horizontal = 8.dp),
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
            Spacer(modifier = Modifier.width(4.dp))
        }
        Text(
            text = text,
            color = textColor,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold
        )
    }
}
