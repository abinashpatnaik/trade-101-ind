package com.example.tradingagent.data

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Thin wrapper around [SharedPreferences] for persisting user settings.
 *
 * Usage:
 * ```
 * val settings = SettingsManager(applicationContext)
 * settings.serverUrl = "http://192.168.1.10:3001"
 * ```
 *
 * A [settingsVersion] [StateFlow] is bumped on every write so that Compose
 * UIs can observe changes reactively without needing a full data class copy.
 */
class SettingsManager(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    // ── Observable version counter ───────────────────────────────────
    private val _settingsVersion = MutableStateFlow(0L)

    /** Incremented on every write; collect to trigger recomposition. */
    val settingsVersion: StateFlow<Long> = _settingsVersion.asStateFlow()

    // ── Server URL ───────────────────────────────────────────────────

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
        set(value) = put { putString(KEY_SERVER_URL, value) }

    // ── Credentials ──────────────────────────────────────────────────

    var username: String?
        get() = prefs.getString(KEY_USERNAME, null)
        set(value) = put { putString(KEY_USERNAME, value) }

    var password: String?
        get() = prefs.getString(KEY_PASSWORD, null)
        set(value) = put { putString(KEY_PASSWORD, value) }

    // ── Refresh interval (seconds) ───────────────────────────────────

    var refreshIntervalSeconds: Int
        get() = prefs.getInt(KEY_REFRESH_INTERVAL, DEFAULT_REFRESH_SECONDS)
        set(value) = put { putInt(KEY_REFRESH_INTERVAL, value) }

    // ── Dark mode ────────────────────────────────────────────────────
    // null → follow system, true → force dark, false → force light

    var darkMode: Boolean?
        get() = when (prefs.getInt(KEY_DARK_MODE, DARK_MODE_SYSTEM)) {
            DARK_MODE_ON -> true
            DARK_MODE_OFF -> false
            else -> null
        }
        set(value) = put {
            putInt(
                KEY_DARK_MODE,
                when (value) {
                    true -> DARK_MODE_ON
                    false -> DARK_MODE_OFF
                    null -> DARK_MODE_SYSTEM
                }
            )
        }

    // ── Helpers ──────────────────────────────────────────────────────

    /** Whether Basic-Auth credentials have been configured. */
    val hasCredentials: Boolean
        get() = !username.isNullOrBlank() && !password.isNullOrBlank()

    /** Clear all saved credentials. */
    fun clearCredentials() {
        prefs.edit()
            .remove(KEY_USERNAME)
            .remove(KEY_PASSWORD)
            .apply()
        _settingsVersion.value++
    }

    /** Reset every preference to its default value. */
    fun resetToDefaults() {
        prefs.edit().clear().apply()
        _settingsVersion.value++
    }

    // ── Private write helper ─────────────────────────────────────────

    private inline fun put(block: SharedPreferences.Editor.() -> Unit) {
        prefs.edit().apply(block).apply()
        _settingsVersion.value++
    }

    // ── Constants ────────────────────────────────────────────────────

    companion object {
        private const val PREFS_NAME = "trading_agent_prefs"

        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_USERNAME = "username"
        private const val KEY_PASSWORD = "password"
        private const val KEY_REFRESH_INTERVAL = "refresh_interval_seconds"
        private const val KEY_DARK_MODE = "dark_mode"

        const val DEFAULT_SERVER_URL = "http://10.0.2.2:3001"
        const val DEFAULT_REFRESH_SECONDS = 5

        // Tri-state for dark mode stored as Int
        private const val DARK_MODE_SYSTEM = 0
        private const val DARK_MODE_ON = 1
        private const val DARK_MODE_OFF = 2
    }
}
