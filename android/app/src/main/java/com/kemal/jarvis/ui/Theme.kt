package com.kemal.jarvis.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

object C {
    val bg = Color(0xFF070D12)
    val surface = Color(0xFF0E1820)
    val surface2 = Color(0xFF14232D)
    val line = Color(0xFF1F3340)
    val accent = Color(0xFF43E9BB)
    val speak = Color(0xFF5CC8FF)
    val think = Color(0xFFFFC266)
    val danger = Color(0xFFFF6B6B)
    val text = Color(0xFFE6F1F0)
    val sub = Color(0xFF8FA6A8)
    val dim = Color(0xFF4F6670)
}

@Composable
fun JarvisTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = C.accent, onPrimary = C.bg, secondary = C.speak, background = C.bg, onBackground = C.text,
            surface = C.surface, onSurface = C.text, surfaceVariant = C.surface2, onSurfaceVariant = C.sub,
            error = C.danger, outline = C.line,
        ),
        content = content,
    )
}
