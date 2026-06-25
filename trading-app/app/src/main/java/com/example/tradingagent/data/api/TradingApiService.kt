package com.example.tradingagent.data.api

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.Credentials
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/**
 * Low-level HTTP service for the Trading Agent dashboard REST API.
 *
 * Every public method is a `suspend` function that:
 *  - runs the network call on [Dispatchers.IO],
 *  - returns `null` (for objects) or an empty list (for arrays) on any error,
 *  - uses [kotlinx.serialization] with `ignoreUnknownKeys` for forward-compat.
 *
 * @param baseUrl  Root URL of the dashboard, e.g. "http://10.0.2.2:3001".
 * @param username Optional Basic-Auth username (null = no auth header).
 * @param password Optional Basic-Auth password.
 */
class TradingApiService(
    private var baseUrl: String,
    private var username: String? = null,
    private var password: String? = null
) {

    // ── JSON parser ──────────────────────────────────────────────────
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        coerceInputValues = true
    }

    // ── OkHttp client shared across all calls ────────────────────────
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .build()

    // ── Configuration helpers ────────────────────────────────────────

    /** Update the server base URL at runtime (e.g. from Settings screen). */
    fun updateBaseUrl(url: String) {
        baseUrl = url.trimEnd('/')
    }

    /** Update credentials at runtime. Pass null to disable auth. */
    fun updateCredentials(user: String?, pass: String?) {
        username = user
        password = pass
    }

    // ── Public API methods ───────────────────────────────────────────

    suspend fun getPortfolio(): PortfolioResponse? =
        getAndDecode("/api/portfolio")

    suspend fun getPositions(): List<Position> =
        getAndDecodeList("/api/positions")

    suspend fun getSignals(): List<Signal> =
        getAndDecodeList("/api/signals")

    suspend fun getTradesToday(): List<Trade> =
        getAndDecodeList("/api/trades")

    suspend fun getAllTrades(): List<Trade> =
        getAndDecodeList("/api/trades/all")

    suspend fun getTicker(): List<TickerItem> =
        getAndDecodeList("/api/ticker")

    suspend fun getAppsHealth(): AppsHealth? =
        getAndDecode("/api/apps-health")

    suspend fun getMarketConfig(): MarketConfig? =
        getAndDecode("/api/market-config")

    // ── Internal helpers ─────────────────────────────────────────────

    /**
     * GET [path], deserialise the response body as [T], or return `null` on
     * any HTTP / parsing / IO error.
     */
    private suspend inline fun <reified T> getAndDecode(path: String): T? {
        return withContext(Dispatchers.IO) {
            try {
                val body = executeGet(path) ?: return@withContext null
                json.decodeFromString<T>(body)
            } catch (e: Exception) {
                // Log in debug builds; silently return null in release.
                e.printStackTrace()
                null
            }
        }
    }

    /**
     * GET [path], deserialise as a `List<T>`, or return an empty list on error.
     */
    private suspend inline fun <reified T> getAndDecodeList(path: String): List<T> {
        return withContext(Dispatchers.IO) {
            try {
                val body = executeGet(path) ?: return@withContext emptyList()
                json.decodeFromString<List<T>>(body)
            } catch (e: Exception) {
                e.printStackTrace()
                emptyList()
            }
        }
    }

    /**
     * Execute a GET request and return the raw response body string, or `null`
     * if the request fails or the server returns a non-2xx status.
     */
    private fun executeGet(path: String): String? {
        val url = "${baseUrl.trimEnd('/')}$path"
        val requestBuilder = Request.Builder().url(url).get()

        // Attach Basic-Auth header when credentials are configured.
        val user = username
        val pass = password
        if (!user.isNullOrBlank() && !pass.isNullOrBlank()) {
            requestBuilder.header("Authorization", Credentials.basic(user, pass))
        }

        val response = client.newCall(requestBuilder.build()).execute()
        return if (response.isSuccessful) {
            response.body?.string()
        } else {
            response.body?.close()
            null
        }
    }
}
