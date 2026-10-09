package com.kemal.jarvis

import android.Manifest
import android.app.StatusBarManager
import android.app.role.RoleManager
import android.appwidget.AppWidgetManager
import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.drawable.Icon
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings as AndroidSettings
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.lifecycleScope
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.kemal.jarvis.access.TalkTileService
import com.kemal.jarvis.access.TalkWidget
import com.kemal.jarvis.core.SecureStore
import com.kemal.jarvis.core.Settings
import com.kemal.jarvis.mac.MacLink
import com.kemal.jarvis.mac.MacRules
import com.kemal.jarvis.ui.HomeScreen
import com.kemal.jarvis.ui.JarvisTheme
import com.kemal.jarvis.ui.Onboarding
import com.kemal.jarvis.ui.OnboardingView
import com.kemal.jarvis.ui.SettingsActions
import com.kemal.jarvis.ui.SettingsScreen
import com.kemal.jarvis.ui.SettingsView
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity(), SettingsActions {
    private val app get() = JarvisApp.of(this)
    private enum class Screen { HOME, SETTINGS, ONBOARDING }
    private var screen by mutableStateOf(Screen.HOME)
    private var focusTyping by mutableStateOf(false)
    private var busy by mutableStateOf<String?>(null)
    private var message by mutableStateOf<String?>(null)
    /** Ayarlar/onboarding görünümünü (izinler vb.) tazelemek için. */
    private var refresh by mutableIntStateOf(0)

    private var permissionWaiter: CompletableDeferred<Boolean>? = null
    private val permissionLauncher = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        permissionWaiter?.complete(granted); permissionWaiter = null; refresh++
    }
    private var afterMic: (() -> Unit)? = null
    private val micLauncher = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        refresh++
        if (granted) afterMic?.invoke() else message = "Mikrofon izni verilmedi. Yazarak kullanabilirsin; izni Ayarlar › İzinler'den açabilirsin."
        afterMic = null
    }
    private val scanLauncher = registerForActivityResult(ScanContract()) { r ->
        val link = MacRules.parsePairLink(r?.contents, BuildConfig.DEV_ENDPOINTS)
        if (r?.contents == null) return@registerForActivityResult
        if (link == null) message = "Bu QR kodu bir JARVIS eşleştirmesi değil. Mac'te yeni kod oluştur."
        else doPair(link.url, link.code)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(androidx.activity.SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            androidx.activity.SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        if (!app.settings.onboarded) screen = Screen.ONBOARDING
        handleIntent(intent, fresh = savedInstanceState == null)
        setContent {
            JarvisTheme {
                val state by app.conversation.state.collectAsState()
                val level by app.conversation.level.collectAsState()
                val mac by app.mac.status.collectAsState()
                BackHandler(screen == Screen.SETTINGS) { screen = Screen.HOME; message = null }
                when (screen) {
                    Screen.ONBOARDING -> { refresh; Onboarding(onboardingView(),
                        onKey = { screen = Screen.SETTINGS },
                        onMic = { micLauncher.launch(Manifest.permission.RECORD_AUDIO) },
                        onNotifications = { if (Build.VERSION.SDK_INT >= 33) permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS) },
                        onContacts = { permissionLauncher.launch(Manifest.permission.READ_CONTACTS) },
                        onDone = { app.settings.onboarded = true; app.migratedNotice = null; screen = Screen.HOME }) }
                    Screen.SETTINGS -> { refresh; SettingsScreen(settingsView(mac), this, Settings.VOICES) { screen = if (app.settings.onboarded) Screen.HOME else Screen.ONBOARDING; message = null } }
                    Screen.HOME -> HomeScreen(state, level, mac, focusTyping,
                        onOrbTap = { if (state.phase == com.kemal.jarvis.conv.Conversation.Phase.IDLE) talk() else app.conversation.interrupt() },
                        onStop = { app.conversation.stop(null) },
                        onMic = { if (state.phase == com.kemal.jarvis.conv.Conversation.Phase.IDLE) talk() else withMic { app.conversation.toggleMic() } },
                        onSend = { app.conversation.sendText(it) },
                        onSettings = { screen = Screen.SETTINGS },
                        onMacTap = { screen = Screen.SETTINGS; app.mac.retryNow() },
                        onConfirm = { id, ok -> app.conversation.answerConfirm(id, ok) },
                        onErrorDismiss = { app.conversation.clearError() },
                        onErrorSettings = { app.conversation.clearError(); screen = Screen.SETTINGS })
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent, fresh = true)
    }

    private val asker = PermissionAsker { p -> askPermission(p) }

    override fun onStart() {
        super.onStart()
        app.permissionAsker = asker
        app.mac.acquire()
        refresh++
    }

    override fun onStop() {
        app.mac.release()
        // Yan tuş paneli (AssistActivity) o sırada açılmış olabilir: yalnız kendi soranını kaldır.
        if (app.permissionAsker === asker) app.permissionAsker = null
        permissionWaiter?.complete(false); permissionWaiter = null
        super.onStop()
    }

    private suspend fun askPermission(p: String): Boolean {
        if (checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED) return true
        val waiter = CompletableDeferred<Boolean>()
        runOnUiThread { permissionWaiter?.complete(false); permissionWaiter = waiter; permissionLauncher.launch(p) }
        return waiter.await()
    }

    private fun handleIntent(intent: Intent?, fresh: Boolean) {
        if (intent == null || !fresh) return
        when (intent.action) {
            Intent.ACTION_ASSIST, Intent.ACTION_VOICE_COMMAND, ACTION_TALK, "android.intent.action.SEARCH_LONG_PRESS" -> {
                if (screen != Screen.ONBOARDING) { screen = Screen.HOME; talk() }
            }
            ACTION_TYPE -> { screen = Screen.HOME; focusTyping = true }
            Intent.ACTION_VIEW -> intent.data?.toString()?.let { raw ->
                val link = MacRules.parsePairLink(raw, BuildConfig.DEV_ENDPOINTS)
                screen = Screen.SETTINGS
                if (link == null) message = "Eşleştirme bağlantısı geçersiz. Mac'te yeni kod oluştur."
                else doPair(link.url, link.code)
            }
        }
        // Bir kez işlendi; döndürme vb. ile tekrar konuşma başlamasın.
        setIntent(Intent(this, MainActivity::class.java))
    }

    private fun talk() = withMic { app.conversation.start(true) }

    private fun withMic(block: () -> Unit) {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) block()
        else { afterMic = block; micLauncher.launch(Manifest.permission.RECORD_AUDIO) }
    }

    // ── Görünümler ─────────────────────────────────────────────────────────────────────────

    private fun granted(p: String) = checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    private fun onboardingView() = OnboardingView(
        hasKey = app.store.get(SecureStore.API_KEY).isNotEmpty(),
        mic = granted(Manifest.permission.RECORD_AUDIO),
        notifications = Build.VERSION.SDK_INT < 33 || granted(Manifest.permission.POST_NOTIFICATIONS),
        contacts = granted(Manifest.permission.READ_CONTACTS),
        migrated = app.migratedNotice,
    )

    private fun settingsView(mac: MacLink.Status) = SettingsView(
        hasKey = app.store.get(SecureStore.API_KEY).isNotEmpty(),
        mac = mac,
        macHost = app.mac.pairing()?.url?.let { Uri.parse(it).host.orEmpty() }.orEmpty(),
        voice = app.settings.voice, halfDuplex = app.settings.halfDuplex, webSearch = app.settings.webSearch,
        idle = app.settings.idleMinutes, owner = app.settings.ownerName, callChannel = app.settings.callChannel,
        isAssistant = isAssistant(), canOverlay = AndroidSettings.canDrawOverlays(this),
        tileSupported = Build.VERSION.SDK_INT >= 33,
        notes = app.memory.allNotes(), turns = app.memory.turns().size,
        version = BuildConfig.VERSION_NAME, busy = busy, message = message,
    )

    private fun isAssistant(): Boolean = if (Build.VERSION.SDK_INT >= 29) {
        getSystemService(RoleManager::class.java)?.isRoleHeld(RoleManager.ROLE_ASSISTANT) == true
    } else AndroidSettings.Secure.getString(contentResolver, "assistant")?.contains(packageName) == true

    // ── SettingsActions ────────────────────────────────────────────────────────────────────

    private fun work(label: String, block: suspend () -> String) {
        busy = label; message = null
        lifecycleScope.launch {
            message = try { block() } catch (e: MacLink.MacError) { e.message } catch (e: Exception) { "İşlem yapılamadı." }
            busy = null; refresh++
        }
    }

    override fun saveKey(key: String) {
        app.store.put(SecureStore.API_KEY, key.trim())
        message = "Gemini anahtarı kaydedildi."; refresh++
    }

    override fun deleteKey() { app.store.remove(SecureStore.API_KEY); message = "Anahtar telefondan silindi."; refresh++ }

    override fun importKeyFromMac() = work("Mac'ten alınıyor…") { app.mac.importApiKey(); "Gemini anahtarı Mac'ten aktarıldı." }

    override fun scanQr() {
        scanLauncher.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setPrompt("Mac'teki JARVIS eşleştirme QR kodunu okut")
            .setBeepEnabled(false).setOrientationLocked(false))
    }

    override fun pairManual(url: String, code: String) = doPair(url, code)

    private fun doPair(url: String, code: String) = work("Mac ile eşleşiyor…") {
        val p = app.mac.pair(url, code, MacLink.deviceName())
        app.mac.retryNow()
        "Eşleşti: ${p.name}." + if (app.store.get(SecureStore.API_KEY).isEmpty()) " Gemini anahtarını \"Mac'ten aktar\" ile alabilirsin." else ""
    }

    override fun unpair() = work("Eşleşme kaldırılıyor…") { app.mac.unpair() }
    override fun retryMac() { app.mac.retryNow(); message = "Yeniden deneniyor." }
    override fun setVoice(v: String) { app.settings.voice = v; message = "Ses: $v (sonraki konuşmada)."; refresh++ }
    override fun setHalfDuplex(on: Boolean) { app.settings.halfDuplex = on; refresh++ }
    override fun setWebSearch(on: Boolean) { app.settings.webSearch = on; refresh++ }
    override fun setIdle(min: Int) { app.settings.idleMinutes = min; refresh++ }
    override fun setOwner(name: String) { app.settings.ownerName = name; message = "Ad kaydedildi."; refresh++ }
    override fun setCallChannel(c: String) { app.settings.callChannel = c; refresh++ }

    override fun openAssistantSettings() {
        val tries = listOf(Intent(AndroidSettings.ACTION_VOICE_INPUT_SETTINGS), Intent(AndroidSettings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS),
            Intent(AndroidSettings.ACTION_SETTINGS))
        for (i in tries) try { startActivity(i); message = "\"Dijital asistan uygulaması\" (Digital assistant app) bölümünde JARVIS'i seç."; return } catch (_: ActivityNotFoundException) {}
    }

    override fun addWidget() {
        val awm = getSystemService(AppWidgetManager::class.java)
        message = if (awm != null && awm.isRequestPinAppWidgetSupported &&
            awm.requestPinAppWidget(ComponentName(this, TalkWidget::class.java), null, null))
            "Ana ekrana eklemek için açılan pencerede \"Ekle\"ye dokun."
        else "Ana ekranda boş bir yere uzun bas › Widget'lar › JARVIS."
    }

    override fun addTile() {
        if (Build.VERSION.SDK_INT >= 33) {
            getSystemService(StatusBarManager::class.java).requestAddTileService(ComponentName(this, TalkTileService::class.java),
                "JARVIS", Icon.createWithResource(this, R.drawable.ic_tile), mainExecutor) { }
            message = "Açılan pencerede \"Kutucuk ekle\"ye dokun."
        } else message = "Bildirim panelini iki kez aşağı çek › kalem (düzenle) › JARVIS kutucuğunu sürükle."
    }

    override fun openOverlaySettings() {
        try { startActivity(Intent(AndroidSettings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"))) }
        catch (_: ActivityNotFoundException) { openAppSettings() }
    }

    override fun openAppSettings() {
        startActivity(Intent(AndroidSettings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
    }

    override fun forgetNote(id: String) {
        app.memory.forget(id, queueForMac = app.mac.isPaired)
        refresh++
        // Ortak hafıza: silme Mac'e de iletilir (Mac kapalıysa bağlanınca).
        if (app.mac.isPaired) lifecycleScope.launch { app.mac.sync(); refresh++ }
    }
    override fun clearConversations() { app.memory.clearConversations(); app.conversation.clearLines(); message = "Konuşma geçmişi silindi."; refresh++ }

    companion object {
        const val ACTION_TALK = "com.kemal.jarvis.TALK"
        const val ACTION_TYPE = "com.kemal.jarvis.TYPE"
    }
}
