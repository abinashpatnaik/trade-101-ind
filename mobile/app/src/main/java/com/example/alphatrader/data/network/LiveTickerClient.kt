package com.example.alphatrader.data.network

import android.util.Log
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.json.JSONObject
import java.util.concurrent.TimeUnit

data class LiveTickEvent(
    val symbol: String,
    val price: Double
)

object LiveTickerClient {
    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS) // SSE needs 0 timeout for infinite stream
        .build()

    fun streamTicks(baseUrl: String): Flow<LiveTickEvent> = callbackFlow {
        val request = Request.Builder()
            .url("$baseUrl/api/stream")
            .header("Accept", "text/event-stream")
            .build()

        val listener = object : EventSourceListener() {
            override fun onOpen(eventSource: EventSource, response: Response) {
                Log.d("LiveTickerClient", "SSE Connection Opened")
            }

            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                try {
                    val json = JSONObject(data)
                    val symbol = json.getString("symbol")
                    val price = json.getDouble("price")
                    trySend(LiveTickEvent(symbol, price))
                } catch (e: Exception) {
                    Log.e("LiveTickerClient", "Error parsing SSE event: $data", e)
                }
            }

            override fun onClosed(eventSource: EventSource) {
                Log.d("LiveTickerClient", "SSE Connection Closed")
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                Log.e("LiveTickerClient", "SSE Connection Failed", t)
                // Flow will be closed, caller should retry if needed
                close(t)
            }
        }

        val factory = EventSources.createFactory(client)
        val eventSource = factory.newEventSource(request, listener)

        awaitClose {
            Log.d("LiveTickerClient", "Closing SSE Connection")
            eventSource.cancel()
        }
    }
}
