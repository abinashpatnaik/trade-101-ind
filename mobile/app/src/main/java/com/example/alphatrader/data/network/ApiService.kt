package com.example.alphatrader.data.network

import retrofit2.http.GET
import retrofit2.http.Query
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import com.example.alphatrader.BuildConfig

// Mock response models based on the Node.js Express endpoints structure
data class PortfolioResponse(
    val nav: Double,
    val cash: Double,
    val buyingPower: Double,
    val dailyPnl: Double,
    val dailyPnlPct: Double,
    val openPositions: Int,
    val maxPositions: Int,
    val winRate: Double,
    val tradesToday: Int,
    val lifetimeRealizedPnl: Double,
    val agentStatus: String,
    val marketOpen: Boolean
)

data class SignalResponse(
    val symbol: String,
    val price: Double,
    val trendScore: Double,
    val signal: String,
    val aiReason: String?,
    val holdReason: String?,
    val confidence: Double
)

data class TradeResponse(
    val date: String,
    val time: String,
    val symbol: String,
    val action: String,
    val price: Double,
    val pnl: String?
)

data class TickerWrapper(val ticker: List<TickerNetworkItem>)
data class TickerNetworkItem(val symbol: String, val price: Double, val change_pct: Double)

data class RiskMetrics(
    val var95: Double,
    val beta: Double,
    val maxDrawdown: Double,
    val volatility: Double
)

data class AnalyticsResponse(
    val risk: RiskMetrics
)

data class NavHistoryItem(
    val date: String,
    val nav: Double
)

data class NavHistoryResponse(
    val data: List<NavHistoryItem>,
    val period: String
)

interface ApiService {
    @GET("/api/portfolio")
    suspend fun getPortfolio(): PortfolioResponse

    @GET("/api/signals")
    suspend fun getSignals(): List<SignalResponse>
    
    @GET("/api/trades")
    suspend fun getTrades(): List<TradeResponse>
    
    @GET("/api/logs")
    suspend fun getLogs(): List<String>
    
    @GET("/api/ticker")
    suspend fun getTickers(): TickerWrapper
    
    @GET("/api/analytics")
    suspend fun getAnalytics(): AnalyticsResponse
    
    @GET("/api/nav-history")
    suspend fun getNavHistory(@Query("period") period: String = "1y"): NavHistoryResponse
}

object RetrofitClient {
    private val usInstance: ApiService by lazy {
        Retrofit.Builder()
            .baseUrl(BuildConfig.US_API_BASE_URL)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }

    private val inInstance: ApiService by lazy {
        Retrofit.Builder()
            .baseUrl(BuildConfig.IN_API_BASE_URL)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }

    fun getInstance(market: String = "US"): ApiService {
        return if (market == "IN") inInstance else usInstance
    }
}
