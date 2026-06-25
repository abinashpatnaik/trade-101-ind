package com.example.tradingagent.ui

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Insights
import androidx.compose.material.icons.filled.Receipt
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.TrendingUp
import androidx.compose.material.icons.outlined.Dashboard
import androidx.compose.material.icons.outlined.Insights
import androidx.compose.material.icons.outlined.Receipt
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.TrendingUp
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import com.example.tradingagent.ui.home.HomeScreen
import com.example.tradingagent.ui.positions.PositionsScreen
import com.example.tradingagent.ui.settings.SettingsScreen
import com.example.tradingagent.ui.signals.SignalsScreen
import com.example.tradingagent.ui.trades.TradesScreen

private data class BottomNavItem(
    val label: String,
    val selectedIcon: ImageVector,
    val unselectedIcon: ImageVector,
)

private val bottomNavItems = listOf(
    BottomNavItem("Home", Icons.Filled.Dashboard, Icons.Outlined.Dashboard),
    BottomNavItem("Positions", Icons.Filled.TrendingUp, Icons.Outlined.TrendingUp),
    BottomNavItem("Signals", Icons.Filled.Insights, Icons.Outlined.Insights),
    BottomNavItem("Trades", Icons.Filled.Receipt, Icons.Outlined.Receipt),
    BottomNavItem("Settings", Icons.Filled.Settings, Icons.Outlined.Settings),
)

@Composable
fun TradingApp(modifier: Modifier = Modifier) {
    var selectedTab by rememberSaveable { mutableIntStateOf(0) }

    Scaffold(
        modifier = modifier.fillMaxSize(),
        bottomBar = {
            NavigationBar(
                containerColor = MaterialTheme.colorScheme.surface,
                tonalElevation = 3.dp,
            ) {
                bottomNavItems.forEachIndexed { index, item ->
                    NavigationBarItem(
                        selected = selectedTab == index,
                        onClick = { selectedTab = index },
                        icon = {
                            Icon(
                                imageVector = if (selectedTab == index) {
                                    item.selectedIcon
                                } else {
                                    item.unselectedIcon
                                },
                                contentDescription = item.label,
                            )
                        },
                        label = { Text(item.label) },
                    )
                }
            }
        },
    ) { innerPadding ->
        val screenModifier = Modifier.padding(innerPadding)
        when (selectedTab) {
            0 -> HomeScreen(modifier = screenModifier)
            1 -> PositionsScreen(modifier = screenModifier)
            2 -> SignalsScreen(modifier = screenModifier)
            3 -> TradesScreen(modifier = screenModifier)
            4 -> SettingsScreen(modifier = screenModifier)
        }
    }
}
