package com.kemal.jarvis.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kemal.jarvis.R
import com.kemal.jarvis.core.Memory
import com.kemal.jarvis.mac.MacLink

/** Ayarlar ekranının ihtiyaç duyduğu işlemler (MainActivity sağlar). */
interface SettingsActions {
    fun saveKey(key: String)
    fun deleteKey()
    fun importKeyFromMac()
    fun scanQr()
    fun pairManual(url: String, code: String)
    fun unpair()
    fun retryMac()
    fun setVoice(v: String)
    fun setHalfDuplex(on: Boolean)
    fun setWebSearch(on: Boolean)
    fun setIdle(min: Int)
    fun setOwner(name: String)
    fun setCallChannel(c: String)
    fun openAssistantSettings()
    fun addWidget()
    fun addTile()
    fun openOverlaySettings()
    fun openAppSettings()
    fun forgetNote(id: String)
    fun clearConversations()
}

data class SettingsView(
    val hasKey: Boolean,
    val mac: MacLink.Status,
    val macHost: String,
    val voice: String,
    val halfDuplex: Boolean,
    val webSearch: Boolean,
    val idle: Int,
    val owner: String,
    val callChannel: String,
    val isAssistant: Boolean,
    val canOverlay: Boolean,
    val tileSupported: Boolean,
    val notes: List<Memory.Note>,
    val turns: Int,
    val version: String,
    val busy: String?,
    val message: String?,
)

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SettingsScreen(v: SettingsView, a: SettingsActions, voices: List<String>, onBack: () -> Unit) {
    Column(Modifier.fillMaxSize().background(C.bg).statusBarsPadding().navigationBarsPadding().imePadding()) {
        Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(painterResource(R.drawable.ic_back), "Geri", tint = C.text) }
            Text("Ayarlar", color = C.text, fontSize = 20.sp, fontWeight = FontWeight.Bold)
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)) {
            v.message?.let { Note(it, C.accent) }
            v.busy?.let { Note(it, C.think) }

            Section("Gemini anahtarı") {
                Text(if (v.hasKey) "Kayıtlı ✓ (telefonda şifreli saklanır)" else "Kayıtlı değil — JARVIS'in konuşabilmesi için gerekli.",
                    color = if (v.hasKey) C.accent else C.think, fontSize = 13.sp)
                var key by remember { mutableStateOf("") }
                Field(key, { key = it.trim() }, "Yeni anahtarı yapıştır", secret = true)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Primary("KAYDET", enabled = key.length >= 20) { a.saveKey(key); key = "" }
                    if (v.mac.state != MacLink.State.UNPAIRED) Secondary("MAC'TEN AKTAR") { a.importKeyFromMac() }
                    if (v.hasKey) Secondary("SİL") { a.deleteKey() }
                }
                Hint("Anahtar yalnız Google'a gönderilir; ekranda gösterilmez.")
            }

            Section("Mac bağlantısı") {
                val (label, color) = when (v.mac.state) {
                    MacLink.State.UNPAIRED -> "Eşleşmemiş" to C.sub
                    MacLink.State.CONNECTING -> "Bağlanıyor…" to C.think
                    MacLink.State.ONLINE -> "Bağlı · ${v.mac.name}" to C.accent
                    MacLink.State.NO_AGENT -> "Bağlı ama Mac'teki JARVIS kapalı" to C.think
                    MacLink.State.OFFLINE -> "Çevrimdışı · yeniden deneniyor" to C.sub
                    MacLink.State.REVOKED -> "Mac bu telefonun erişimini kaldırdı" to C.danger
                }
                Text(label, color = color, fontSize = 14.sp)
                if (v.mac.detail.isNotBlank()) Hint(v.mac.detail)
                if (v.macHost.isNotBlank()) Hint(v.macHost)
                if (v.mac.state == MacLink.State.UNPAIRED || v.mac.state == MacLink.State.REVOKED) {
                    Hint("Bilgisayarla eşleştir: Mac'te JARVIS › TELEFON panelini aç, \"ANDROID UYGULAMASINI EŞLEŞTİR\"e bas; çıkan QR kodu buradan tara ya da adresi ve 8 haneli kodu elle gir.")
                    Primary("QR KODU TARA") { a.scanQr() }
                    var url by remember { mutableStateOf("") }
                    var code by remember { mutableStateOf("") }
                    Field(url, { url = it.trim() }, "https://…trycloudflare.com")
                    Field(code, { code = it.filter(Char::isDigit).take(8) }, "8 haneli kod", number = true)
                    Secondary("ELLE EŞLEŞTİR", enabled = url.isNotEmpty() && code.length == 8) { a.pairManual(url, code) }
                } else {
                    Hint("Mac bağlıyken JARVIS tek konuşmada hem telefonda hem Mac'te iş yapar (Mac uygulamaları, Apple Takvim, dosyalar, hava durumu). Mac'teki \"ara ve söyle\" istekleri de buraya gelir.")
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Secondary("YENİDEN DENE") { a.retryMac() }
                        Secondary("EŞLEŞMEYİ KALDIR") { a.unpair() }
                    }
                }
            }

            Section("Ses ve konuşma") {
                Text("JARVIS'in sesi", color = C.text, fontSize = 14.sp)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    voices.forEach { Chip(it, it == v.voice) { a.setVoice(it) } }
                }
                Toggle("JARVIS konuşurken beni dinleme", "Hoparlörden JARVIS kendi sesini duyup sözünü kesiyorsa aç. Açıkken susturmak için küreye dokunursun.",
                    v.halfDuplex, a::setHalfDuplex)
                Toggle("Web araması", "Güncel bilgiler için Google Arama kullanılır (Google kotasından düşer).", v.webSearch, a::setWebSearch)
                Text("Sessizlikte otomatik kapanma", color = C.text, fontSize = 14.sp)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(1, 2, 5, 10).forEach { m -> Chip("$m dk", v.idle == m) { a.setIdle(m) } }
                }
                var owner by remember(v.owner) { mutableStateOf(v.owner) }
                Field(owner, { owner = it.take(40) }, "Adın")
                if (owner.isNotBlank() && owner != v.owner) Secondary("ADI KAYDET") { a.setOwner(owner) }
            }

            Section("Kolay erişim") {
                Toggle("Telefonun asistanı olsun", "Güç tuşuna ya da ana ekran tuşuna uzun basınca JARVIS açılır ve dinler. Açılan ekranda \"Varsayılan dijital asistan uygulaması\"nı JARVIS yap.",
                    v.isAssistant) { a.openAssistantSettings() }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Secondary("ANA EKRANA WIDGET") { a.addWidget() }
                    Secondary(if (v.tileSupported) "HIZLI AYARLARA EKLE" else "HIZLI AYAR KUTUCUĞU") { a.addTile() }
                }
                Hint("Uygulama simgesine uzun basınca da \"Konuş\" ve \"Yaz\" kısayolları çıkar.")
                Toggle("Arka planda uygulama açabilsin", "\"Diğer uygulamaların üzerinde göster\" izni. Verilmezse ekran kapalıyken JARVIS bir uygulamayı açamaz, dokununca açılan bildirim gösterir.",
                    v.canOverlay) { a.openOverlaySettings() }
            }

            Section("Ara ve söyle") {
                Hint("Mesaj, arama sırasında hoparlörden hangi kanalla çalınsın? (1.x'teki ses testi sonucun korunur.)")
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("media" to "Medya", "alarm" to "Alarm", "voice" to "Görüşme").forEach { (k, l) -> Chip(l, v.callChannel == k) { a.setCallChannel(k) } }
                }
                if (v.callChannel == "none") Hint("Ses testinde karşı taraf hiçbir kanalı duymamıştı.")
            }

            Section("Ortak hafıza (telefon + Mac)") {
                if (v.notes.isEmpty()) Hint("Henüz not yok. \"Bunu hatırla: …\" diyebilirsin; Mac ile eşleşmişse Mac'teki JARVIS de bilir.")
                v.notes.takeLast(30).reversed().forEach { n ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("• " + n.text, color = C.text, fontSize = 13.sp, modifier = Modifier.weight(1f))
                        IconButton(onClick = { a.forgetNote(n.text) }) { Icon(painterResource(R.drawable.ic_close), "Sil", tint = C.dim) }
                    }
                }
                if (v.turns > 0) Secondary("KONUŞMA GEÇMİŞİNİ SİL (${v.turns})") { a.clearConversations() }
            }

            Section("Uygulama") {
                Hint("JARVIS ${v.version}")
                Secondary("İZİNLER (ANDROID AYARLARI)") { a.openAppSettings() }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(C.surface).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(title.uppercase(), color = C.accent, fontSize = 12.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.5.sp)
        content()
    }
}

@Composable private fun Hint(t: String) = Text(t, color = C.sub, fontSize = 12.sp, lineHeight = 17.sp)

@Composable
private fun Note(t: String, color: Color) = Text(t, color = C.text, fontSize = 13.sp,
    modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(color.copy(alpha = 0.15f)).padding(12.dp))

@Composable
private fun Field(value: String, onChange: (String) -> Unit, placeholder: String, secret: Boolean = false, number: Boolean = false) {
    OutlinedTextField(value, onChange, modifier = Modifier.fillMaxWidth(), singleLine = true,
        placeholder = { Text(placeholder, color = C.dim) },
        visualTransformation = if (secret) PasswordVisualTransformation() else androidx.compose.ui.text.input.VisualTransformation.None,
        keyboardOptions = KeyboardOptions(keyboardType = if (number) KeyboardType.Number else if (secret) KeyboardType.Password else KeyboardType.Uri),
        colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = C.accent, unfocusedBorderColor = C.line,
            focusedTextColor = C.text, unfocusedTextColor = C.text, cursorColor = C.accent))
}

@Composable
private fun Primary(t: String, enabled: Boolean = true, onClick: () -> Unit) =
    Button(onClick, enabled = enabled, colors = ButtonDefaults.buttonColors(containerColor = C.accent, contentColor = C.bg)) {
        Text(t, fontWeight = FontWeight.Bold, fontSize = 13.sp)
    }

@Composable
private fun Secondary(t: String, enabled: Boolean = true, onClick: () -> Unit) =
    OutlinedButton(onClick, enabled = enabled) { Text(t, color = if (enabled) C.text else C.dim, fontSize = 12.sp) }

@Composable
private fun Chip(t: String, selected: Boolean, onClick: () -> Unit) =
    Text(t, color = if (selected) C.bg else C.text, fontSize = 13.sp,
        modifier = Modifier.clip(RoundedCornerShape(16.dp)).background(if (selected) C.accent else C.surface2)
            .border(1.dp, if (selected) C.accent else C.line, RoundedCornerShape(16.dp))
            .clickable(onClick = onClick).padding(horizontal = 12.dp, vertical = 7.dp))

@Composable
private fun Toggle(title: String, hint: String, on: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().clickable { onChange(!on) }, verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, color = C.text, fontSize = 14.sp)
            Text(hint, color = C.sub, fontSize = 12.sp, lineHeight = 16.sp)
        }
        Spacer(Modifier.width(8.dp))
        Switch(on, onChange, colors = SwitchDefaults.colors(checkedTrackColor = C.accent, checkedThumbColor = C.bg))
    }
}
