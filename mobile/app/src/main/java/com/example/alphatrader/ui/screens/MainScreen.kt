package com.example.alphatrader.ui.screens

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Assessment
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.compose.runtime.collectAsState
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.alphatrader.theme.BgSurface
import com.example.alphatrader.theme.BrandGreen
import com.example.alphatrader.theme.TextDisabled
import com.example.alphatrader.ui.components.AgentStatus
import com.example.alphatrader.ui.components.AlphaTopAppBar
import com.example.alphatrader.ui.viewmodels.DashboardViewModel

sealed class Screen(val route: String, val title: String, val icon: androidx.compose.ui.graphics.vector.ImageVector) {
    object Dashboard : Screen("dashboard", "Dashboard", Icons.Filled.ShowChart)
    object Signals : Screen("signals", "Signals", Icons.Filled.Assessment)
    object Logs : Screen("logs", "Logs", Icons.Filled.History)
}

@Composable
fun MainScreen(viewModel: DashboardViewModel = viewModel()) {
    val state by viewModel.uiState.collectAsState()
    val navController = rememberNavController()
    val items = listOf(Screen.Dashboard, Screen.Signals, Screen.Logs)

    Scaffold(
        topBar = { 
            AlphaTopAppBar(
                marketRegion = state.marketRegion,
                onMarketToggle = { viewModel.toggleMarket() },
                status = state.agentStatus,
                statusText = when(state.agentStatus) {
                    AgentStatus.LIVE -> "Live 10:45"
                    AgentStatus.SLEEPING -> "Observing"
                    else -> "Closed"
                }
            )
        },
        bottomBar = {
            NavigationBar(
                containerColor = BgSurface,
                contentColor = TextDisabled
            ) {
                val navBackStackEntry by navController.currentBackStackEntryAsState()
                val currentDestination = navBackStackEntry?.destination

                items.forEach { screen ->
                    NavigationBarItem(
                        icon = { Icon(screen.icon, contentDescription = screen.title) },
                        label = { Text(screen.title) },
                        selected = currentDestination?.hierarchy?.any { it.route == screen.route } == true,
                        onClick = {
                            navController.navigate(screen.route) {
                                // Pop up to the start destination of the graph to
                                // avoid building up a large stack of destinations
                                popUpTo(navController.graph.findStartDestination().id) {
                                    saveState = true
                                }
                                // Avoid multiple copies of the same destination
                                launchSingleTop = true
                                // Restore state when reselecting a previously selected item
                                restoreState = true
                            }
                        },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = BrandGreen,
                            selectedTextColor = BrandGreen,
                            unselectedIconColor = TextDisabled,
                            unselectedTextColor = TextDisabled,
                            indicatorColor = BgSurface
                        )
                    )
                }
            }
        }
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = Screen.Dashboard.route,
            modifier = Modifier.padding(innerPadding)
        ) {
            composable(Screen.Dashboard.route) { DashboardScreen() }
            composable(Screen.Signals.route) { SignalsScreen() }
            composable(Screen.Logs.route) { LogsScreen() }
        }
    }
}
