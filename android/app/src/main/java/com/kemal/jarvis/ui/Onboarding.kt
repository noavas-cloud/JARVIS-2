package com.kemal.jarvis.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kemal.jarvis.R

data class OnboardingView(val hasKey: Boolean, val mic: Boolean, val notifications: Boolean, val contacts: Boolean, val migrated: String?)

@Composable
fun Onboarding(
    v: OnboardingView,
    onKey: () -> Unit,
    onMic: () -> Unit,
    onNotifications: () -> Unit,
    onContacts: () -> Unit,
    onDone: () -> Unit,
) {
    Column(Modifier.fillMaxSize().background(C.bg).statusBarsPadding().navigationBarsPadding().verticalScroll(rememberScrollState())
        .padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Spacer(Modifier.height(12.dp))
        Image(painterResource(R.drawable.jarvis_logo_small), null, Modifier.size(96.dp).clip(CircleShape))
        Text("JARVIS 2.0", color = C.text, fontSize = 26.sp, fontWeight = FontWeight.Bold, letterSpacing = 3.sp)
        Text("Artık canlı konuşuyor: dokun, konuş, sözünü kes. Telefonunda ve (bağlıysa) Mac'inde iş yapar.",
            color = C.sub, fontSize = 14.sp, lineHeight = 20.sp)
        v.migrated?.let { Text(it, color = C.accent, fontSize = 13.sp) }
        Step("1", "Gemini anahtarı", if (v.hasKey) "Hazır ✓" else "JARVIS'in düşünüp konuşması için. Ayarlar'dan gir ya da Mac'ten aktar.",
            v.hasKey, if (v.hasKey) null else "GİR", onKey)
        Step("2", "Mikrofon", if (v.mic) "İzin verildi ✓" else "Seninle konuşabilmem için. Yalnız konuşma açıkken dinlerim.", v.mic, if (v.mic) null else "İZİN VER", onMic)
        Step("3", "Bildirimler", if (v.notifications) "İzin verildi ✓" else "Dinlerken görünen bildirim ve onay istekleri için.", v.notifications,
            if (v.notifications) null else "İZİN VER", onNotifications)
        Step("4", "Rehber (isteğe bağlı)", if (v.contacts) "İzin verildi ✓" else "\"Annemi ara\", \"Ali'ye mesaj yaz\" için.", v.contacts,
            if (v.contacts) null else "İZİN VER", onContacts)
        Spacer(Modifier.height(8.dp))
        Button(onDone, Modifier.fillMaxWidth().height(52.dp), colors = ButtonDefaults.buttonColors(containerColor = C.accent, contentColor = C.bg)) {
            Text("BAŞLA", fontWeight = FontWeight.Bold, fontSize = 16.sp)
        }
    }
}

@Composable
private fun Step(n: String, title: String, text: String, done: Boolean, action: String?, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(C.surface).padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(n, color = if (done) C.bg else C.accent, fontWeight = FontWeight.Bold,
            modifier = Modifier.clip(CircleShape).background(if (done) C.accent else C.surface2).padding(horizontal = 11.dp, vertical = 5.dp))
        Column(Modifier.weight(1f).padding(horizontal = 12.dp)) {
            Text(title, color = C.text, fontSize = 15.sp, fontWeight = FontWeight.Bold)
            Text(text, color = C.sub, fontSize = 12.sp, lineHeight = 16.sp)
        }
        action?.let { OutlinedButton(onClick) { Text(it, color = C.accent, fontSize = 12.sp) } }
    }
}
