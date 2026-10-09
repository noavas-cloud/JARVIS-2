package com.kemal.jarvis.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kemal.jarvis.R
import com.kemal.jarvis.conv.Conversation
import com.kemal.jarvis.conv.Conversation.Phase
import com.kemal.jarvis.conv.Conversation.Who
import com.kemal.jarvis.mac.MacLink
import kotlinx.coroutines.delay

@OptIn(ExperimentalFoundationApi::class, androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
fun HomeScreen(
    state: Conversation.State,
    level: Float,
    mac: MacLink.Status,
    focusTyping: Boolean,
    onOrbTap: () -> Unit,
    onStop: () -> Unit,
    onMic: () -> Unit,
    onSend: (String) -> Unit,
    onSettings: () -> Unit,
    onMacTap: () -> Unit,
    onConfirm: (Long, Boolean) -> Unit,
    onErrorDismiss: () -> Unit,
    onErrorSettings: () -> Unit,
) {
    var text by rememberSaveable { mutableStateOf("") }
    val focus = remember { FocusRequester() }
    LaunchedEffect(focusTyping) { if (focusTyping) try { focus.requestFocus() } catch (_: Exception) {} }
    val listState = rememberLazyListState()
    val shown = state.lines.size + (if (state.partialUser.isNotBlank()) 1 else 0) + (if (state.partialJarvis.isNotBlank()) 1 else 0)
    LaunchedEffect(shown, state.partialJarvis.length, state.partialUser.length) { if (shown > 0) listState.animateScrollToItem(shown - 1) }

    Column(Modifier.fillMaxSize().background(C.bg).statusBarsPadding().navigationBarsPadding().imePadding()) {
        // Üst çubuk
        Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("JARVIS", color = C.text, fontSize = 20.sp, fontWeight = FontWeight.Bold, letterSpacing = 4.sp)
            Spacer(Modifier.weight(1f))
            MacChip(mac, onMacTap)
            IconButton(onClick = onSettings) { Icon(painterResource(R.drawable.ic_settings), "Ayarlar", tint = C.sub) }
        }

        // Küre
        val compact = state.lines.isNotEmpty() || state.partialUser.isNotBlank()
        val ime = WindowInsets.isImeVisible
        Box(Modifier.fillMaxWidth().height(if (ime) 110.dp else if (compact) 210.dp else 320.dp)
            .semantics { contentDescription = "JARVIS küresi" }
            .combinedClickable(interactionSource = remember { MutableInteractionSource() }, indication = null,
                onClick = onOrbTap, onLongClick = onStop), contentAlignment = Alignment.Center) {
            Orb(state.phase, level)
        }
        Text(statusText(state), color = statusColor(state.phase), fontSize = 14.sp, letterSpacing = 1.sp,
            modifier = Modifier.align(Alignment.CenterHorizontally).padding(bottom = 6.dp))

        // Hata
        state.error?.let { err ->
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp).clip(RoundedCornerShape(12.dp))
                .background(C.danger.copy(alpha = 0.14f)).padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(err, color = C.text, fontSize = 13.sp, modifier = Modifier.weight(1f))
                if ("anahtar" in err.lowercase() || "izin" in err.lowercase())
                    Text("AYARLAR", color = C.accent, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                        modifier = Modifier.clickable(onClick = onErrorSettings).padding(8.dp))
                IconButton(onClick = onErrorDismiss, modifier = Modifier.size(32.dp)) { Icon(painterResource(R.drawable.ic_close), "Kapat", tint = C.sub) }
            }
        }

        // Sohbet
        Box(Modifier.weight(1f).fillMaxWidth()) {
            if (shown == 0 && state.phase == Phase.IDLE) {
                Column(Modifier.align(Alignment.Center).padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Küreye dokun ve konuş", color = C.sub, fontSize = 15.sp)
                    Spacer(Modifier.height(6.dp))
                    Text("\"Feneri aç\" · \"Yarın 7'de alarm kur\" · \"Annemi ara\" · \"Mac'te Spotify'ı aç\"",
                        color = C.dim, fontSize = 12.sp, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
                }
            }
            LazyColumn(state = listState, modifier = Modifier.fillMaxSize().padding(horizontal = 14.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)) {
                items(state.lines, key = { it.id }) { Bubble(it.who, it.text, partial = false) }
                if (state.partialUser.isNotBlank()) item(key = "pu") { Bubble(Who.USER, state.partialUser, partial = true) }
                if (state.partialJarvis.isNotBlank()) item(key = "pj") { Bubble(Who.JARVIS, state.partialJarvis, partial = true) }
            }
        }

        // Onay kartı
        state.confirm?.let { ConfirmCard(it, onConfirm) }

        // Giriş çubuğu
        Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            TextField(value = text, onValueChange = { text = it.take(4000) }, modifier = Modifier.weight(1f).focusRequester(focus),
                placeholder = { Text("Yaz…", color = C.dim) }, singleLine = false, maxLines = 4,
                shape = RoundedCornerShape(22.dp),
                colors = TextFieldDefaults.colors(focusedContainerColor = C.surface2, unfocusedContainerColor = C.surface,
                    focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent,
                    focusedTextColor = C.text, unfocusedTextColor = C.text, cursorColor = C.accent),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { if (text.isNotBlank()) { onSend(text); text = "" } }))
            Spacer(Modifier.width(6.dp))
            if (text.isNotBlank()) {
                RoundButton(R.drawable.ic_send, "Gönder", C.accent, C.bg) { onSend(text); text = "" }
            } else {
                val idle = state.phase == Phase.IDLE
                RoundButton(if (state.micOn || idle) R.drawable.ic_mic else R.drawable.ic_mic_off,
                    if (state.micOn) "Mikrofonu kapat" else if (idle) "Konuş" else "Mikrofonu aç",
                    if (state.micOn) C.accent else C.surface2, if (state.micOn) C.bg else C.text, onMic)
            }
            if (state.phase != Phase.IDLE) {
                Spacer(Modifier.width(6.dp))
                RoundButton(R.drawable.ic_stop, "Konuşmayı bitir", C.surface2, C.danger, onStop)
            }
        }
    }
}

@Composable
private fun RoundButton(icon: Int, label: String, bg: Color, fg: Color, onClick: () -> Unit) {
    Box(Modifier.size(48.dp).clip(CircleShape).background(bg).clickable(onClick = onClick)
        .semantics { contentDescription = label }, contentAlignment = Alignment.Center) {
        Icon(painterResource(icon), null, tint = fg, modifier = Modifier.size(22.dp))
    }
}

@Composable
private fun MacChip(mac: MacLink.Status, onClick: () -> Unit) {
    val (label, color) = when (mac.state) {
        MacLink.State.UNPAIRED -> "Bilgisayarla eşleştir" to C.dim
        MacLink.State.CONNECTING -> "Mac bağlanıyor" to C.think
        MacLink.State.ONLINE -> "Mac bağlı" to C.accent
        MacLink.State.NO_AGENT -> "Mac · JARVIS kapalı" to C.think
        MacLink.State.OFFLINE -> "Mac çevrimdışı" to C.sub
        MacLink.State.REVOKED -> "Mac erişimi kaldırıldı" to C.danger
    }
    Row(Modifier.clip(RoundedCornerShape(14.dp)).border(1.dp, C.line, RoundedCornerShape(14.dp)).clickable(onClick = onClick)
        .padding(horizontal = 10.dp, vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(7.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(6.dp))
        Text(label, color = C.sub, fontSize = 11.sp)
    }
}

@Composable
private fun Bubble(who: Who, text: String, partial: Boolean) {
    when (who) {
        Who.USER -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Text(text, color = C.text, fontSize = 15.sp, fontStyle = if (partial) FontStyle.Italic else FontStyle.Normal,
                modifier = Modifier.widthIn(max = 300.dp).clip(RoundedCornerShape(16.dp, 16.dp, 4.dp, 16.dp))
                    .background(C.surface2).padding(horizontal = 12.dp, vertical = 8.dp))
        }
        Who.JARVIS -> Text(text, color = C.text, fontSize = 15.sp, lineHeight = 21.sp,
            fontStyle = if (partial) FontStyle.Italic else FontStyle.Normal,
            modifier = Modifier.fillMaxWidth().padding(end = 40.dp, top = 2.dp, bottom = 2.dp))
        Who.ACTION -> Text("⚙  $text", color = C.accent.copy(alpha = 0.85f), fontSize = 12.sp, modifier = Modifier.padding(vertical = 1.dp))
        Who.NOTICE -> Text(text, color = C.sub, fontSize = 12.sp,
            modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(C.surface).padding(10.dp))
    }
}

@Composable
internal fun ConfirmCard(c: Conversation.Confirm, onConfirm: (Long, Boolean) -> Unit) {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(c.id) { while (true) { now = System.currentTimeMillis(); delay(500) } }
    val left = ((c.expiresAt - now) / 1000).coerceAtLeast(0)
    Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp).clip(RoundedCornerShape(18.dp))
        .background(C.surface2).border(1.dp, C.accent.copy(alpha = 0.5f), RoundedCornerShape(18.dp)).padding(16.dp)) {
        Text(c.title, color = C.text, fontSize = 17.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))
        c.lines.forEach { Text(it, color = C.sub, fontSize = 14.sp, modifier = Modifier.padding(vertical = 2.dp)) }
        Spacer(Modifier.height(12.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("${left} sn", color = C.dim, fontSize = 12.sp)
            Spacer(Modifier.weight(1f))
            OutlinedButton(onClick = { onConfirm(c.id, false) }) { Text("VAZGEÇ", color = C.text) }
            Spacer(Modifier.width(8.dp))
            Button(onClick = { onConfirm(c.id, true) }, colors = ButtonDefaults.buttonColors(containerColor = C.accent, contentColor = C.bg)) {
                Text(c.confirmLabel, fontWeight = FontWeight.Bold)
            }
        }
    }
}

internal fun statusText(s: Conversation.State): String = when (s.phase) {
    Phase.IDLE -> "DOKUN VE KONUŞ"
    Phase.CONNECTING -> "BAĞLANIYOR…"
    Phase.LISTENING -> if (s.micOn) "DİNLİYORUM" else "MİKROFON KAPALI · YAZABİLİRSİN"
    Phase.THINKING -> "DÜŞÜNÜYORUM"
    Phase.SPEAKING -> if (s.halfDuplex) "KONUŞUYOR · SUSTURMAK İÇİN DOKUN" else "KONUŞUYOR"
}

internal fun statusColor(p: Phase) = when (p) {
    Phase.IDLE -> C.sub; Phase.CONNECTING -> C.sub; Phase.LISTENING -> C.accent; Phase.THINKING -> C.think; Phase.SPEAKING -> C.speak
}
