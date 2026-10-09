package com.kemal.jarvis

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.kemal.jarvis.conv.Conversation.Phase
import com.kemal.jarvis.ui.AssistOverlay
import com.kemal.jarvis.ui.JarvisTheme
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/**
 * Yan tuş / dijital asistan: ChatGPT'deki gibi o an açık uygulamanın üstünde yalnız konuşma küresi.
 * Boşluğa dokunmak, geri tuşu ya da küreye uzun basmak küreyi kapatır ve konuşmayı bitirir.
 * JARVIS başka bir uygulama açarsa konuşma arka planda (bildirimle) sürer, tıpkı ana ekrandaki gibi.
 */
class AssistActivity : ComponentActivity() {
    private val app get() = JarvisApp.of(this)
    private var closing = false

    private var permissionWaiter: CompletableDeferred<Boolean>? = null
    private val permissionLauncher = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        permissionWaiter?.complete(granted); permissionWaiter = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        if (!canTalkHere()) return
        if (savedInstanceState == null) talk()
        setContent {
            JarvisTheme {
                val state by app.conversation.state.collectAsState()
                val level by app.conversation.level.collectAsState()
                BackHandler { dismiss() }
                AssistOverlay(state, level,
                    onDismiss = { dismiss() },
                    onOrbTap = { if (state.phase == Phase.IDLE) talk() else app.conversation.interrupt() },
                    onConfirm = { id, ok -> app.conversation.answerConfirm(id, ok) },
                    onErrorDismiss = { app.conversation.clearError() })
            }
        }
        // Konuşma kendiliğinden biterse (sessizlik süresi, bildirimden "Bitir") küre de kapanır.
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                var wasActive = app.conversation.active
                app.conversation.state.collectLatest { s ->
                    if (s.phase != Phase.IDLE) { wasActive = true; return@collectLatest }
                    if (!wasActive || s.error != null || s.confirm != null || closing) return@collectLatest
                    delay(1200)
                    closing = true
                    finish()
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        closing = false
        if (canTalkHere()) talk()
    }

    private val asker = PermissionAsker { p -> askPermission(p) }

    override fun onStart() {
        super.onStart()
        app.permissionAsker = asker
        app.mac.acquire()
    }

    override fun onStop() {
        app.mac.release()
        // Yalnız kendi soranını kaldır: ana ekran o sırada açılmış olabilir.
        if (app.permissionAsker === asker) app.permissionAsker = null
        permissionWaiter?.complete(false); permissionWaiter = null
        super.onStop()
    }

    /** İlk kurulum bitmemişse ya da mikrofon izni yoksa normal JARVIS ekranı açılır (izinleri o sorar). */
    private fun canTalkHere(): Boolean {
        val micOk = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (app.settings.onboarded && micOk) return true
        closing = true
        startActivity(Intent(this, MainActivity::class.java).setAction(MainActivity.ACTION_TALK)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP))
        finish()
        return false
    }

    private fun talk() { if (!app.conversation.active) app.conversation.start(true) }

    private fun dismiss() {
        if (closing) return
        closing = true
        app.conversation.stop(null)
        finish()
    }

    private suspend fun askPermission(p: String): Boolean {
        if (checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED) return true
        val waiter = CompletableDeferred<Boolean>()
        runOnUiThread { permissionWaiter?.complete(false); permissionWaiter = waiter; permissionLauncher.launch(p) }
        return waiter.await()
    }
}
