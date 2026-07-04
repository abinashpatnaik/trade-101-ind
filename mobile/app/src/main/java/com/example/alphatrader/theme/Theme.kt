package com.example.alphatrader.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat
import com.example.alphatrader.data.ThemeMode

private val DarkColorScheme = darkColorScheme(
    primary = BrandGreen,
    secondary = BrandBlue,
    tertiary = BrandPurple,
    background = BgPrimary,
    surface = BgSurface,
    surfaceVariant = BgSurfaceRaised,
    onPrimary = TextPrimary,
    onSecondary = TextPrimary,
    onTertiary = TextPrimary,
    onBackground = TextPrimary,
    onSurface = TextPrimary,
    onSurfaceVariant = TextSecondary,
    error = BrandRed
)

private val LightColorScheme = lightColorScheme(
    primary = BrandGreen,
    secondary = BrandBlue,
    tertiary = BrandPurple,
    background = BgPrimaryLight,
    surface = BgSurfaceLight,
    surfaceVariant = BgSurfaceRaisedLight,
    onPrimary = TextPrimaryLight,
    onSecondary = TextPrimaryLight,
    onTertiary = TextPrimaryLight,
    onBackground = TextPrimaryLight,
    onSurface = TextPrimaryLight,
    onSurfaceVariant = TextSecondaryLight,
    error = BrandRed
)

@Composable
fun AlphaTraderTheme(
    themeMode: ThemeMode = ThemeMode.SYSTEM,
    content: @Composable () -> Unit
) {
    val isDark = when (themeMode) {
        ThemeMode.SYSTEM -> isSystemInDarkTheme()
        ThemeMode.DARK -> true
        ThemeMode.LIGHT -> false
    }

    val colorScheme = if (isDark) DarkColorScheme else LightColorScheme
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = colorScheme.background.toArgb()
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !isDark
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = Typography,
        content = content
    )
}
