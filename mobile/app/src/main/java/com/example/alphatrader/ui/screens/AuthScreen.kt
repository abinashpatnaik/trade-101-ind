package com.example.alphatrader.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.example.alphatrader.data.SessionManager

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AuthScreen(
    sessionManager: SessionManager,
    onLoginSuccess: () -> Unit
) {
    var serverUrl by remember { mutableStateOf(sessionManager.serverUrl) }
    var username by remember { mutableStateOf(sessionManager.username) }
    var password by remember { mutableStateOf(sessionManager.password) }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text("AlphaTrader Login") })
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            OutlinedTextField(
                value = serverUrl,
                onValueChange = { serverUrl = it },
                label = { Text("Server URL (e.g. http://10.0.2.2)") },
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
            )
            Spacer(modifier = Modifier.height(16.dp))

            OutlinedTextField(
                value = username,
                onValueChange = { username = it },
                label = { Text("Username") },
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(modifier = Modifier.height(16.dp))

            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text("Password") },
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password)
            )
            Spacer(modifier = Modifier.height(32.dp))

            Button(
                onClick = {
                    sessionManager.serverUrl = serverUrl
                    sessionManager.username = username
                    sessionManager.password = password
                    onLoginSuccess()
                },
                modifier = Modifier.fillMaxWidth().height(50.dp)
            ) {
                Text("Login")
            }
        }
    }
}
