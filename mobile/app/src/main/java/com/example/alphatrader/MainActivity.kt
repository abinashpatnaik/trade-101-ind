package com.example.alphatrader

import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.example.alphatrader.data.SessionManager
import com.example.alphatrader.data.network.RetrofitClient
import com.example.alphatrader.data.PollingService
import com.example.alphatrader.theme.AlphaTraderTheme
import com.example.alphatrader.ui.screens.AuthScreen
import com.example.alphatrader.ui.screens.BiometricLockScreen
import com.example.alphatrader.ui.screens.MainScreen
import androidx.compose.runtime.collectAsState

class MainActivity : FragmentActivity() {

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { _: Boolean ->
        // Proceed even if denied for now
    }

    private var isBiometricUnlocked by mutableStateOf(false)
    private var biometricErrorMessage by mutableStateOf<String?>(null)
    private var biometricAvailable by mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val sessionManager = SessionManager.getInstance(this)
        RetrofitClient.initialize(sessionManager)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestPermissionLauncher.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }

        if (sessionManager.isLoggedIn) {
            startPollingService()
        }

        // Check if biometrics are available
        val biometricManager = BiometricManager.from(this)
        biometricAvailable = when (biometricManager.canAuthenticate(
            BiometricManager.Authenticators.BIOMETRIC_STRONG or BiometricManager.Authenticators.BIOMETRIC_WEAK
        )) {
            BiometricManager.BIOMETRIC_SUCCESS -> true
            else -> false
        }

        // If biometrics not available, skip the lock screen
        if (!biometricAvailable) {
            isBiometricUnlocked = true
        }

        enableEdgeToEdge()
        setContent {
            val themeMode by sessionManager.themeMode.collectAsState()

            AlphaTraderTheme(themeMode = themeMode) {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    val navController = rememberNavController()
                    val isLoggedIn = sessionManager.isLoggedIn

                    // Determine start destination
                    val startDestination = when {
                        !isLoggedIn -> "auth"
                        isLoggedIn && biometricAvailable && !isBiometricUnlocked -> "biometric_lock"
                        else -> "main"
                    }

                    NavHost(navController = navController, startDestination = startDestination) {
                        composable("auth") {
                            AuthScreen(
                                sessionManager = sessionManager,
                                onLoginSuccess = {
                                    startPollingService()
                                    if (biometricAvailable) {
                                        isBiometricUnlocked = false
                                        navController.navigate("biometric_lock") {
                                            popUpTo("auth") { inclusive = true }
                                        }
                                        promptBiometric {
                                            navController.navigate("main") {
                                                popUpTo("biometric_lock") { inclusive = true }
                                            }
                                        }
                                    } else {
                                        navController.navigate("main") {
                                            popUpTo("auth") { inclusive = true }
                                        }
                                    }
                                }
                            )
                        }
                        composable("biometric_lock") {
                            val lifecycleOwner = androidx.lifecycle.compose.LocalLifecycleOwner.current
                            
                            androidx.compose.runtime.DisposableEffect(lifecycleOwner) {
                                val observer = androidx.lifecycle.LifecycleEventObserver { _, event ->
                                    if (event == androidx.lifecycle.Lifecycle.Event.ON_RESUME) {
                                        if (!isBiometricUnlocked) {
                                            promptBiometric {
                                                navController.navigate("main") {
                                                    popUpTo("biometric_lock") { inclusive = true }
                                                }
                                            }
                                        }
                                    }
                                }
                                lifecycleOwner.lifecycle.addObserver(observer)
                                onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
                            }

                            BiometricLockScreen(
                                onAuthenticateClick = { 
                                    promptBiometric {
                                        navController.navigate("main") {
                                            popUpTo("biometric_lock") { inclusive = true }
                                        }
                                    }
                                },
                                errorMessage = biometricErrorMessage
                            )
                        }
                        composable("main") {
                            MainScreen(
                                themeMode = themeMode,
                                onThemeChange = { newMode -> sessionManager.setThemeMode(newMode) }
                            )
                        }
                    }
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Removed auto-prompt from here. It is now handled in the Composable's LifecycleEventObserver.
    }

    private fun promptBiometric(onSuccess: () -> Unit) {
        val executor = ContextCompat.getMainExecutor(this)

        val callback = object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                super.onAuthenticationSucceeded(result)
                isBiometricUnlocked = true
                biometricErrorMessage = null
                onSuccess()
            }

            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                super.onAuthenticationError(errorCode, errString)
                if (errorCode != BiometricPrompt.ERROR_USER_CANCELED &&
                    errorCode != BiometricPrompt.ERROR_NEGATIVE_BUTTON &&
                    errorCode != BiometricPrompt.ERROR_CANCELED) {
                    biometricErrorMessage = errString.toString()
                }
            }

            override fun onAuthenticationFailed() {
                super.onAuthenticationFailed()
                biometricErrorMessage = "Authentication failed. Try again."
            }
        }

        val biometricPrompt = BiometricPrompt(this, executor, callback)

        val promptInfo = BiometricPrompt.PromptInfo.Builder()
            .setTitle("AlphaTrader")
            .setSubtitle("Authenticate to access your portfolio")
            .setAllowedAuthenticators(
                BiometricManager.Authenticators.BIOMETRIC_STRONG or
                BiometricManager.Authenticators.BIOMETRIC_WEAK or
                BiometricManager.Authenticators.DEVICE_CREDENTIAL
            )
            .build()

        biometricPrompt.authenticate(promptInfo)
    }

    private fun startPollingService() {
        val intent = Intent(this, PollingService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }
}
