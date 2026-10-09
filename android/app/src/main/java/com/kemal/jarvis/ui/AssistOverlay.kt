package com.kemal.jarvis.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kemal.jarvis.R
import com.kemal.jarvis.conv.Conversation
import kotlinx.coroutines.launch

/**
 * Yan tuş: o an açık uygulamanın üstünde yalnız konuşma küresi (panel, yazı, düğme yok; arka plan karartılmaz).
 * Yalnız gerektiğinde kürenin üstünde onay kartı (arama vb.) ya da hata satırı belirir.
 * Boşluğa dokunmak kapatır; küreye dokunmak JARVIS'i susturur, uzun basmak kapatır.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun AssistOverlay(
    state: Conversation.State,
    level: Float,
    onDismiss: () -> Unit,
    onOrbTap: () -> Unit,
    onConfirm: (Long, Boolean) -> Unit,
    onErrorDismiss: () -> Unit,
) {
    // Açılış ("spawn"): küre küçükten hafif yaylanarak büyür ve belirir.
    val grow = remember { Animatable(0.3f) }
    val fade = remember { Animatable(0f) }
    LaunchedEffect(Unit) {
        launch { fade.animateTo(1f, tween(220)) }
        grow.animateTo(1f, spring(dampingRatio = 0.55f, stiffness = Spring.StiffnessMediumLow))
    }
    Box(Modifier.fillMaxSize()
        .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onDismiss)
        .semantics { contentDescription = "Kapatmak için dokun" }) {
        Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth().navigationBarsPadding().padding(bottom = 16.dp),
            horizontalAlignment = Alignment.CenterHorizontally) {
            // Kart ve hata satırına dokunmak küreyi kapatmasın.
            Column(Modifier.fillMaxWidth().clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {}) {
                state.error?.let { err ->
                    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp).clip(RoundedCornerShape(14.dp))
                        .background(C.surface2).padding(start = 14.dp, top = 4.dp, bottom = 4.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Text(err, color = C.text, fontSize = 13.sp, modifier = Modifier.weight(1f))
                        IconButton(onClick = onErrorDismiss) { Icon(painterResource(R.drawable.ic_close), "Kapat", tint = C.sub) }
                    }
                }
                state.confirm?.let { ConfirmCard(it, onConfirm) }
            }
            // Kürenin içi tam koyu (açık renkli uygulamada arkadaki yazı içinden okunmasın), yalnız dış kenarı yumuşakça kaybolur.
            // 2.4.0: yeni kürenin dış halkası da koyu zeminde kalsın diye koyu alan %80 → %88.
            Box(Modifier.size(250.dp)
                .graphicsLayer { scaleX = grow.value; scaleY = grow.value; alpha = fade.value }
                .background(Brush.radialGradient(0f to C.bg, 0.88f to C.bg, 0.95f to C.bg.copy(alpha = 0.6f), 1f to Color.Transparent))
                .semantics { contentDescription = "JARVIS küresi, " + statusText(state).lowercase() }
                .combinedClickable(interactionSource = remember { MutableInteractionSource() }, indication = null,
                    onClick = onOrbTap, onLongClick = onDismiss), contentAlignment = Alignment.Center) {
                Orb(state.phase, level)
            }
        }
    }
}
