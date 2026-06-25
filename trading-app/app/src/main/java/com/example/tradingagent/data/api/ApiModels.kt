package com.example.tradingagent.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// ──────────────────────────────────────────────
// GET /api/portfolio
// ──────────────────────────────────────────────

@Serializable
data class PortfolioResponse(
    val nav: Double = 0.0,
    val cash: Double = 0.0,
    @SerialName("dailyPnl") val dailyPnl: Double = 0.0,
    @SerialName("dailyPnlPct") val dailyPnlPct: Double = 0.0,
    @SerialName("openPositions") val openPositions: Int = 0,
    @SerialName("maxPositions") val maxPositions: Int = 0,
    @SerialName("winRate") val winRate: Double = 0.0,
    @SerialName("tradesToday") val tradesToday: Int = 0,
    @SerialName("agentStatus") val agentStatus: String = "offline",
    @SerialName("marketOpen") val marketOpen: Boolean = false,
    @SerialName("lastUpdated") val lastUpdated: String = ""
)

// ──────────────────────────────────────────────
// GET /api/positions — array of Position
// Supports both IBKR-style and Alpaca-style fields.
// ──────────────────────────────────────────────

@Serializable
data class Position(
    // IBKR-style fields
    val conid: Int? = null,
    @SerialName("contractDesc") val contractDesc: String? = null,
    val position: Double? = null,
    @SerialName("mktPrice") val mktPrice: Double? = null,
    @SerialName("mktValue") val mktValue: Double? = null,
    @SerialName("avgCost") val avgCost: Double? = null,
    @SerialName("avgPrice") val avgPrice: Double? = null,
    @SerialName("unrealizedPnl") val unrealizedPnl: Double? = null,
    @SerialName("realizedPnl") val realizedPnl: Double? = null,

    // Alpaca-style fields (snake_case from API)
    val symbol: String? = null,
    val quantity: String? = null,
    @SerialName("avg_cost") val alpacaAvgCost: String? = null,
    @SerialName("current_price") val currentPrice: String? = null,
    @SerialName("market_value") val marketValue: String? = null,
    @SerialName("unrealized_pl") val unrealizedPl: String? = null
) {
    /** Unified display symbol — prefers Alpaca symbol, falls back to IBKR contractDesc. */
    val displaySymbol: String
        get() = symbol ?: contractDesc ?: "—"

    /** Unified display quantity as a Double. */
    val displayQuantity: Double
        get() = quantity?.toDoubleOrNull() ?: position ?: 0.0

    /** Unified market value as a Double. */
    val displayMarketValue: Double
        get() = marketValue?.toDoubleOrNull() ?: mktValue ?: 0.0

    /** Unified unrealized P&L as a Double. */
    val displayUnrealizedPnl: Double
        get() = unrealizedPl?.toDoubleOrNull() ?: unrealizedPnl ?: 0.0

    /** Unified current price as a Double. */
    val displayCurrentPrice: Double
        get() = currentPrice?.toDoubleOrNull() ?: mktPrice ?: 0.0

    /** Unified average cost as a Double. */
    val displayAvgCost: Double
        get() = alpacaAvgCost?.toDoubleOrNull() ?: avgCost ?: avgPrice ?: 0.0
}

// ──────────────────────────────────────────────
// GET /api/signals — array of Signal
// ──────────────────────────────────────────────

@Serializable
data class Signal(
    val symbol: String,
    val price: Double? = null,
    @SerialName("changePct") val changePct: Double? = null,
    val rsi: Double? = null,
    @SerialName("trendScore") val trendScore: Double? = null,
    @SerialName("macdSignal") val macdSignal: String? = null,
    @SerialName("emaSignal") val emaSignal: String? = null,
    @SerialName("combinedScore") val combinedScore: Double? = null,
    val signal: String? = null,          // "BUY", "SELL", "HOLD"
    val confidence: Double? = null,
    @SerialName("buyThreshold") val buyThreshold: Double? = null,
    @SerialName("sellThreshold") val sellThreshold: Double? = null,
    @SerialName("aiDecision") val aiDecision: String? = null,  // "APPROVED", "REJECTED", "IDLE", "OFF"
    @SerialName("aiReason") val aiReason: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null
)

// ──────────────────────────────────────────────
// GET /api/trades and /api/trades/all — array of Trade
// ──────────────────────────────────────────────

@Serializable
data class Trade(
    val date: String = "",
    val time: String = "",
    val action: String = "",     // "BUY" or "SELL"
    val symbol: String = "",
    val quantity: String? = null,
    val price: String? = null,
    val notional: String? = null,
    val pnl: String? = null,
    @SerialName("exit_reason") val exitReason: String? = null,
    val mode: String? = null     // "paper" or "live"
)

// ──────────────────────────────────────────────
// GET /api/ticker — array of TickerItem
// ──────────────────────────────────────────────

@Serializable
data class TickerItem(
    val symbol: String,
    val price: Double? = null,
    @SerialName("changePercent") val changePercent: Double? = null
)

// ──────────────────────────────────────────────
// GET /api/apps-health
// ──────────────────────────────────────────────

@Serializable
data class AppsHealth(
    val agent: AppStatus = AppStatus(),
    val dashboard: AppStatus = AppStatus()
)

@Serializable
data class AppStatus(
    val status: String = "unknown",
    val container: String? = null
)

// ──────────────────────────────────────────────
// GET /api/market-config
// ──────────────────────────────────────────────

@Serializable
data class MarketConfig(
    val market: String = "US",
    val currency: String? = null,
    @SerialName("currencySymbol") val currencySymbol: String? = null,
    val timezone: String? = null
)
