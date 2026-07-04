package com.example.alphatrader.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.example.alphatrader.theme.*

@Composable
fun FilterChipRow() {
    var selectedChip by remember { mutableStateOf("All Signals") }
    val chips = listOf("● All Signals", "Buy Zone", "Sell Alerts", "Gated")

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        chips.forEach { chipName ->
            val isSelected = chipName.contains(selectedChip) || selectedChip.contains(chipName.replace("● ", ""))
            
            val bgColor = if (isSelected) BrandGreen else MaterialTheme.colorScheme.surfaceVariant
            val textColor = if (isSelected) Color.Black else MaterialTheme.colorScheme.onSurfaceVariant
            val modifier = if (isSelected) {
                Modifier
            } else {
                Modifier.border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(8.dp))
            }

            Box(
                modifier = modifier
                    .height(32.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(bgColor)
                    .clickable { selectedChip = chipName.replace("● ", "") }
                    .padding(horizontal = 14.dp),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = chipName,
                    color = textColor,
                    style = if (isSelected) MaterialTheme.typography.labelMedium.copy(fontWeight = androidx.compose.ui.text.font.FontWeight.Bold) else MaterialTheme.typography.labelMedium
                )
            }
        }
    }
}
