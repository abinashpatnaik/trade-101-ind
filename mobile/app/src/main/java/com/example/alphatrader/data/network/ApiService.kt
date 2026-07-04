package com.example.alphatrader.data.network

import retrofit2.http.GET
import retrofit2.http.Path
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
    val marketOpen: Boolean,
    val nextOpen: String?
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
    val pnl: String?,
    val quantity: Double?
)

data class ChartDataPoint(
    val date: String,
    val price: Double
)

data class StockSummary(
    val totalBought: Double,
    val totalSold: Double,
    val totalPnl: Double
)

data class StockDetailsResponse(
    val chartData: List<ChartDataPoint>,
    val summary: StockSummary,
    val trades: List<TradeResponse>
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

    @GET("api/ticker")
    suspend fun getTicker(): List<TickerNetworkItem>

    @GET("api/stock/{symbol}")
    suspend fun getStockDetails(@Path("symbol") symbol: String): StockDetailsResponse
    
    @GET("/api/ticker")
    suspend fun getTickers(): TickerWrapper
    
    @GET("/api/analytics")
    suspend fun getAnalytics(): AnalyticsResponse
    
    @GET("/api/nav-history")
    suspend fun getNavHistory(@Query("period") period: String = "1y"): NavHistoryResponse
}

object RetrofitClient {
    private var sessionManager: com.example.alphatrader.data.SessionManager? = null

    fun initialize(manager: com.example.alphatrader.data.SessionManager) {
        sessionManager = manager
    }

    private val okHttpClient: okhttp3.OkHttpClient by lazy {
        okhttp3.OkHttpClient.Builder().apply {
            addInterceptor { chain ->
                var request = chain.request()

                val username = sessionManager?.username ?: ""
                val password = sessionManager?.password ?: ""
                if (username.isNotBlank() && password.isNotBlank()) {
                    val credentials = okhttp3.Credentials.basic(username, password)
                    request = request.newBuilder()
                        .header("Authorization", credentials)
                        .build()
                }

                val customUrl = sessionManager?.serverUrl
                if (!customUrl.isNullOrBlank()) {
                    try {
                        val uri = java.net.URI(customUrl)
                        val newUrl = request.url.newBuilder()
                            .scheme(uri.scheme)
                            .host(uri.host)
                            // We intentionally keep the original port (3001 vs 3002)
                            .build()
                        request = request.newBuilder()
                            .url(newUrl)
                            .build()
                    } catch (e: Exception) {
                        // ignore malformed URLs
                    }
                }

                chain.proceed(request)
            }
            val logging = okhttp3.logging.HttpLoggingInterceptor().apply {
                level = okhttp3.logging.HttpLoggingInterceptor.Level.BODY
            }
            addInterceptor(logging)
        }.build()
    }

    private val usInstance: ApiService by lazy {
        Retrofit.Builder()
            .baseUrl(BuildConfig.US_API_BASE_URL)
            .client(okHttpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }

    private val inInstance: ApiService by lazy {
        Retrofit.Builder()
            .baseUrl(BuildConfig.IN_API_BASE_URL)
            .client(okHttpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }

    fun getInstance(market: String = "US"): ApiService {
        return if (market == "IN") inInstance else usInstance
    }
}
