package com.example.tradingagent.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

// ── Premium dark trading scheme (default) ──────────────────────
private val TradingDarkScheme = darkColorScheme(
    primary = CyanAccent,
    onPrimary = NavyDark,
    primaryContainer = Color(0xFF003D4D),
    onPrimaryContainer = CyanAccent,

    secondary = TealAccent,
    onSecondary = NavyDark,
    secondaryContainer = Color(0xFF003D33),
    onSecondaryContainer = TealAccent,

    tertiary = GoldAccent,
    onTertiary = NavyDark,
    tertiaryContainer = Color(0xFF4D3D00),
    onTertiaryContainer = GoldAccent,

    background = NavyDark,
    onBackground = TextPrimary,

    surface = NavySurface,
    onSurface = TextPrimary,
    surfaceVariant = NavyCard,
    onSurfaceVariant = TextSecondary,

    outline = TextMuted,
    outlineVariant = Color(0xFF2A3060),

    inverseSurface = TextPrimary,
    inverseOnSurface = NavyDark,
    inversePrimary = Color(0xFF006B7A),
)

// ── Light fallback ─────────────────────────────────────────────
private val TradingLightScheme = lightColorScheme(
    primary = Color(0xFF006B7A),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFB3F0FF),
    onPrimaryContainer = Color(0xFF001F26),

    secondary = Color(0xFF006B5A),
    onSecondary = Color.White,

    tertiary = Color(0xFF8B6900),
    onTertiary = Color.White,

    background = Color(0xFFF5F7FA),
    onBackground = Color(0xFF1A1C2E),

    surface = Color.White,
    onSurface = Color(0xFF1A1C2E),
    surfaceVariant = Color(0xFFEEF0F5),
    onSurfaceVariant = Color(0xFF44475A),
)

@Composable
fun TradingAgentTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    dynamicColor: Boolean = false, // Disabled: use our curated trading palette
    content: @Composable () -> Unit,
) {
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val context = LocalContext.current
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        }
        darkTheme -> TradingDarkScheme
        else -> TradingLightScheme
    }

    MaterialTheme(colorScheme = colorScheme, typography = Typography, content = content)
}
