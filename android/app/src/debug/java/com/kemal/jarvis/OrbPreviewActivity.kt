package com.kemal.jarvis

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kemal.jarvis.conv.Conversation.Phase
import com.kemal.jarvis.ui.C
import com.kemal.jarvis.ui.Orb
import kotlin.math.abs
import kotlin.math.sin

/** Yalnız debug: kürenin tüm hâlleri tek ekranda (taklit ses düzeyiyle). Release'te yok.
 *  adb shell am start -n com.kemal.jarvis/.OrbPreviewActivity  (-e phase SPEAKING → tek büyük küre) */
class OrbPreviewActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val single = intent.getStringExtra("phase")?.let { runCatching { Phase.valueOf(it) }.getOrNull() }
        setContent {
            val t = remember { mutableFloatStateOf(0f) }
            LaunchedEffect(Unit) { var last = 0L; while (true) withFrameNanos { n -> if (last != 0L) t.floatValue += (n - last) / 1e9f; last = n } }
            val level = (0.35f + 0.65f * abs(sin(t.floatValue * 7f) * sin(t.floatValue * 2.3f))).coerceIn(0f, 1f)
            Box(Modifier.fillMaxSize().background(C.bg)) {
                if (single != null) {
                    Box(Modifier.fillMaxWidth().height(320.dp).align(Alignment.Center)) { Orb(single, level) }
                } else Column(Modifier.fillMaxSize()) {
                    val phases = Phase.values().toList()
                    for (row in phases.chunked(2)) Row(Modifier.fillMaxWidth().weight(1f)) {
                        for (p in row) Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                            Box(Modifier.fillMaxWidth().weight(1f)) { Orb(p, level) }
                            Text(p.name, color = C.sub, fontSize = 11.sp)
                        }
                    }
                }
            }
        }
    }
}
